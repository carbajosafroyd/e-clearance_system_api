# api/utils/cloudinary_utils.py
#
# Every Cloudinary call in the project goes through this module - and, since
# the account split, every call names the account it talks to.
#
# ---------------------------------------------------------------------------
# Accounts are versioned
#
# Free-tier storage runs out, so a deployment adds a Cloudinary account and
# points new uploads at it while everything already uploaded stays where it
# is. `settings.CLOUDINARY_ACCOUNTS` maps an integer version to one account's
# credentials and `settings.CLOUDINARY_CURRENT_VERSION` names the one new
# uploads use. Every model row that holds a public id also holds the version
# it was uploaded under, so reading a URL or deleting the file goes to the
# right account - the same arrangement as a versioned password pepper: the
# current version for new writes, the stored version for old rows.
#
# The SDK's global `cloudinary.config()` is never switched: each call passes
# the account it needs, so two requests on two accounts cannot race.
#
# WHERE an upload goes inside an account - the folder tree keyed by school,
# institute and student, and the tags that name its owner - is decided in
# `paths.py`. Nothing here chooses a folder; it uploads to the one it is
# given and deletes what it is told to.
# ---------------------------------------------------------------------------

import cloudinary.uploader
from cloudinary.utils import cloudinary_url

import cloudinary.api
from typing import List, Literal, NamedTuple, Optional, TypedDict

from django.conf import settings

IMAGE_EXTENSIONS = ["jpg", "jpeg", "png", "webp", "pdf"]
VIDEO_EXTENSIONS = ["mp4", "mov", "avi", "mkv"]

MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5 MB
MAX_VIDEO_SIZE = 50 * 1024 * 1024  # 50 MB


# ---------------------------------------------------------------------------
# Account selection
# ---------------------------------------------------------------------------


def cloudinary_accounts() -> dict:
    """`{version: {"cloud_name", "api_key", "api_secret"}}` from settings."""
    return getattr(settings, "CLOUDINARY_ACCOUNTS", {}) or {}


def current_version() -> int:
    """The account version every NEW upload goes to."""
    return int(getattr(settings, "CLOUDINARY_CURRENT_VERSION", 1) or 1)


def account_options(version: Optional[int] = None) -> dict:
    """
    The credentials for one account version, as keyword arguments the SDK
    accepts on every call (`upload`, `destroy`, `cloudinary_url`, `api.*`).

    `None` means the current version - correct for a new upload, never for
    an existing asset, which must be read and deleted under the version its
    row stores. An unknown version is a configuration error and says so,
    rather than silently talking to the wrong account.
    """
    accounts = cloudinary_accounts()
    if not accounts:
        # Nothing configured (a bare dev box): let the SDK fall back to its
        # own configuration / CLOUDINARY_URL, exactly as before the split.
        return {}
    resolved = current_version() if version is None else int(version)
    try:
        account = accounts[resolved]
    except KeyError:
        raise ValueError(
            f"Cloudinary account version {resolved} is not configured "
            f"(known: {sorted(accounts)}). Add CLOUDINARY_*_{resolved} to the environment."
        )
    return {
        "cloud_name": account["cloud_name"],
        "api_key": account["api_key"],
        "api_secret": account["api_secret"],
    }


def version_for_cloud_name(cloud_name: str) -> Optional[int]:
    """The version whose account owns `cloud_name`, if any."""
    for version, account in cloudinary_accounts().items():
        if account.get("cloud_name") == cloud_name:
            return version
    return None


def discover_version(value, resource_type="image") -> Optional[int]:
    """
    Which account holds an asset whose row does not say.

    A full URL carries the cloud name, so that costs nothing. A bare public
    id is asked of each account (current first, then the rest) through the
    Admin API - rate limited on free tiers, so callers persist the answer on
    the row and never ask twice. `None` when nobody has it.
    """
    if not value or not isinstance(value, str):
        return None
    if not is_public_id(value):
        marker = "res.cloudinary.com/"
        if marker in value:
            cloud_name = value.split(marker, 1)[1].split("/", 1)[0]
            return version_for_cloud_name(cloud_name)
        return None
    accounts = cloudinary_accounts()
    if not accounts:
        return None
    order = [current_version()] + [v for v in sorted(accounts, reverse=True) if v != current_version()]
    for version in order:
        try:
            cloudinary.api.resource(value, resource_type=resource_type, **account_options(version))
            return version
        except cloudinary.api.NotFound:
            continue
        except Exception:  # noqa: BLE001 - a bad account must not stop the search
            continue
    return None


# ---------------------------------------------------------------------------
# Validation and upload (always the CURRENT account)
# ---------------------------------------------------------------------------


def get_file_extension(file):
    return file.name.split(".")[-1].lower()


def validate_file(file):
    ext = get_file_extension(file)

    if ext in IMAGE_EXTENSIONS:
        if file.size > MAX_IMAGE_SIZE:
            raise ValueError(
                f"{file.name} is too large. Max image size is {MAX_IMAGE_SIZE / (1024*1024)} MB."
            )
        return "image"

    elif ext in VIDEO_EXTENSIONS:
        if file.size > MAX_VIDEO_SIZE:
            raise ValueError(
                f"{file.name} is too large. Max video size is {MAX_VIDEO_SIZE / (1024*1024)} MB."
            )
        return "video"

    else:
        raise ValueError(
            f"{file.name} has invalid extension. Allowed: {IMAGE_EXTENSIONS + VIDEO_EXTENSIONS}"
        )


def upload_file(file, folder="uploads/", tags: Optional[List[str]] = None):
    """
    Upload image OR video to the current account.

    `folder` is the asset's permanent address (from `paths.folder_for`) and
    `tags` name its owner (from `paths.tags_for`) so the Media Library and
    the Search API can find it by school, institute, student or user.
    Returns: public_id, resource_type, version (the account it went to), secure_url
    """
    resource_type = validate_file(file)
    version = current_version()

    options = {"folder": folder, "resource_type": resource_type, "overwrite": True}
    if tags:
        options["tags"] = list(tags)

    result = cloudinary.uploader.upload(file, **options, **account_options(version))

    return {
        "public_id": result["public_id"],
        "resource_type": resource_type,
        "version": version,
        "secure_url": result.get("secure_url"),
    }


def upload_files(files, folder="uploads/", tags: Optional[List[str]] = None):
    """Upload multiple media files"""
    results = []
    for file in files:
        results.append(upload_file(file, folder=folder, tags=tags))
    return results


def upload_image(image, folder="uploads/", tags: Optional[List[str]] = None):
    """The public id of an image uploaded to the current account (see `current_version`)."""
    result = upload_file(image, folder, tags=tags)
    if result["resource_type"] != "image":
        raise ValueError("File is not an image")
    return result["public_id"]


class UploadedImage(NamedTuple):
    secure_url: str
    public_id: str
    version: int


def upload_image_versioned(image, folder="uploads/", tags: Optional[List[str]] = None) -> UploadedImage:
    """
    `upload_image` plus the two things a row must store beside the public
    id: the URL, and the account version it was uploaded under.
    """
    result = upload_file(image, folder, tags=tags)
    if result["resource_type"] != "image":
        raise ValueError("File is not an image")
    return UploadedImage(result["secure_url"] or "", result["public_id"], result["version"])


def upload_images(images, folder="uploads/", tags: Optional[List[str]] = None):
    public_ids = []
    for image in images:
        public_ids.append(upload_image(image, folder, tags=tags))
    return public_ids


# ---------------------------------------------------------------------------
# Delete and read (the STORED version of the asset)
# ---------------------------------------------------------------------------


def delete_file(public_id, resource_type="image", version: Optional[int] = None):
    """
    Destroy an asset on the account that holds it.

    `version` is the row's stored version. Passing none makes this look the
    asset up across accounts first - a destroy sent to the wrong account
    quietly answers "not found" and leaves the file (and the bill) behind.
    """
    if version is None:
        version = discover_version(public_id, resource_type=resource_type)
    return cloudinary.uploader.destroy(
        public_id,
        resource_type=resource_type,
        invalidate=True,
        **account_options(version),
    )


def delete_image(public_id, version: Optional[int]):
    """Destroy an image under the account version its row stores."""
    return delete_file(public_id, resource_type="image", version=version)


class FileData(TypedDict, total=False):
    public_id: str
    resource_type: Literal["image", "video"]
    version: int


def delete_files(files_data: List[FileData]):
    """
    Bulk delete multiple media files efficiently.

    Expected input format:
    [
        {"public_id": "uploads/abc123", "resource_type": "image", "version": 1},
        {"public_id": "uploads/xyz789", "resource_type": "video", "version": 2},
    ]

    `version` is the row's stored account version; entries without one are
    looked up first. Deletes are grouped per account and resource type.
    """
    grouped: dict = {}

    for file in files_data:
        res_type = file.get("resource_type", "image")
        if res_type not in ("image", "video"):
            continue
        version = file.get("version")
        if version is None:
            version = discover_version(file["public_id"], resource_type=res_type) # type: ignore
        grouped.setdefault((version, res_type), []).append(file["public_id"]) # type: ignore

    results = {}

    # Execute bulk delete for each account and resource type
    for (version, res_type), public_ids in grouped.items():
        if public_ids:
            response = cloudinary.api.delete_resources(
                public_ids,
                resource_type=res_type,
                invalidate=True,
                **account_options(version),
            )
            results.setdefault(res_type, {})[version] = response

    return results


def delete_by_prefix(prefix: str, resource_types=("image", "video")) -> dict:
    """
    Destroy everything under a folder, on every account.

    The tree in `paths.py` puts everything an owner has under one prefix
    (`schools/1/institutes/2/`), so deleting the owner is one call per
    account and resource type instead of a walk over every row. The prefix
    is given a trailing "/" - `schools/1` would also match `schools/10`. An
    account that refuses (a revoked key, a rate limit) is skipped, not
    fatal: the owner's row is going regardless, and an orphan is the cheaper
    problem. Returns `{resource_type: {version: response}}` for what ran.
    """
    prefix = prefix.rstrip("/") + "/"
    results: dict = {}
    if not getattr(settings, "CLOUDINARY_PREFIX_DELETE_ENABLED", True):
        # Off under the test runner - see the setting in config/settings/base.py.
        return results
    for version in cloudinary_accounts() or {None: None}:
        for res_type in resource_types:
            try:
                response = cloudinary.api.delete_resources_by_prefix(
                    prefix, resource_type=res_type, invalidate=True, **account_options(version)
                )
            except Exception:  # noqa: BLE001 - see docstring
                continue
            results.setdefault(res_type, {})[version] = response
    return results


def get_file_url(public_id, resource_type="image", version: Optional[int] = None, **options):
    """
    A delivery URL for a stored asset.

    The URL embeds the cloud name, so it must be built for the account that
    holds the asset: pass the row's stored `version`. `None` builds it for
    the current account, which is only right for something just uploaded.
    """
    account = account_options(version)
    if account:
        options.setdefault("cloud_name", account["cloud_name"])
    url, _ = cloudinary_url(public_id, resource_type=resource_type, **options)
    return url


# ---------------------------------------------------------------------------
# Replacing and removing single images owned by a model row
#
# Every model that stores one Cloudinary public id (a user's avatar, a
# school's logo, a GCash QR) has to do the same three things correctly, and
# each serializer used to do them in its own order:
#
#   1. upload the new file BEFORE deleting the old one, so a failed upload
#      leaves the row pointing at an image that still exists rather than at
#      nothing;
#   2. never call `destroy` on a value that is not a public id - `School.logo`
#      and `Institute.logo` held plain URLs for a long time, and asking
#      Cloudinary to destroy "https://..." fails loudly for nothing;
#   3. actually delete the old file, which the first version of every one of
#      these forgot at least once - the account is billed by storage, and an
#      orphaned upload per edit adds up.
#
# `replace_image` and `remove_image` are those three things in one place.
# ---------------------------------------------------------------------------

URL_PREFIXES = ("http://", "https://", "//", "data:")


def is_public_id(value) -> bool:
    """True when `value` looks like something Cloudinary gave us, not a URL."""
    if not value or not isinstance(value, str):
        return False
    return not value.startswith(URL_PREFIXES)


def resolve_image_url(value, version: Optional[int] = None, **options) -> str | None:
    """
    A URL for a stored image reference, whichever form it is in.

    Rows written before uploads went through Cloudinary hold absolute URLs;
    rows written since hold public ids. Serializers call this so a client
    never has to know which, and a bad value degrades to None rather than a
    500 on every list that renders the column. `version` is the row's stored
    account version (only public ids need it; a URL already names its host).
    """
    if not value:
        return None
    if not is_public_id(value):
        return value
    try:
        return get_file_url(value, version=version, **options)
    except Exception:  # noqa: BLE001 - a broken reference must not break the row
        return None


def replace_image(
    old_value,
    new_file,
    folder: str,
    old_version: Optional[int] = None,
    tags: Optional[List[str]] = None,
) -> str:
    """
    Upload `new_file` (to the current account), then delete the image
    `old_value` referred to (on `old_version`'s account).

    Returns the new public id; the new image lives under `current_version()`,
    which the caller stores beside it. Upload first: if Cloudinary refuses
    the file the caller's ValueError propagates and the old image is
    untouched. Deletion failures are swallowed - the row already points at
    the new image, and an orphan in Cloudinary is a cheaper problem than a
    save that fails after the upload succeeded.
    """
    public_id = upload_image(new_file, folder=folder, tags=tags)
    remove_image(old_value, version=old_version)
    return public_id


def remove_image(value, version: Optional[int] = None) -> None:
    """Delete the image `value` refers to, if it is one of ours, on its own account."""
    if not is_public_id(value):
        return
    try:
        delete_file(value, version=version)
    except Exception:  # noqa: BLE001 - see replace_image
        pass

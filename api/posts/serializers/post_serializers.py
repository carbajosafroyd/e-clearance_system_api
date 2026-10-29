from drf_spectacular.utils import extend_schema_field
from api.utils.cloudinary.paths import Kind, folder_for, tags_for
from rest_framework import serializers
from ..models import Post, PostMedia, ReactionCount
from api.utils.cloudinary.cloudinary_utils import upload_files
from api.utils.cloudinary.cloudinary_utils import get_file_url
from django.core.cache import cache


class ReactionCountsSerializer(serializers.Serializer):
    """
    Shape of `reaction_counts` on the feed and on comments.

    Documentation only - the value is built as a plain dict, so nothing here
    ever runs. It exists so the schema says what the dict holds instead of
    falling back to "string".
    """

    total = serializers.IntegerField()
    like = serializers.IntegerField()
    heart = serializers.IntegerField()
    haha = serializers.IntegerField()
    sad = serializers.IntegerField()
    angry = serializers.IntegerField()


class PostDetailReactionCountsSerializer(serializers.Serializer):
    """
    The narrower `reaction_counts` the single-post view returns: no `total`,
    no `sad`, and null when the post has no ReactionCount row yet.
    """

    like = serializers.IntegerField()
    heart = serializers.IntegerField()
    haha = serializers.IntegerField()
    angry = serializers.IntegerField()


class PostMediaSerializer(serializers.ModelSerializer):
    media_url = serializers.SerializerMethodField()

    class Meta:
        model = PostMedia
        fields = ["id", "media_type", "media_url", "order_index"]

    def get_media_url(self, obj) -> str:
        if obj.media_type == "image":
            return get_file_url(obj.media_url, version=obj.cloudinary_version)
        if obj.media_type == "video":
            return get_file_url(obj.media_url, resource_type="video", version=obj.cloudinary_version)
        else:
            return obj.media_url


class PostFeedSerializer(serializers.ModelSerializer):
    """
    Optimized for the scrolling feed.
    Includes media items and reaction counts to prevent double-fetching.
    """
    institute = serializers.StringRelatedField()
    # CharField rather than ReadOnlyField: the source is a method, and an
    # untyped ReadOnlyField leaves the schema guessing at its type.
    user_name = serializers.CharField(source="user.get_full_name", read_only=True)
    media = PostMediaSerializer(many=True, source="postmedia_set", read_only=True)
    reaction_counts = serializers.SerializerMethodField()
    comments_count = serializers.SerializerMethodField()
    user_profile = serializers.SerializerMethodField()

    class Meta:
        model = Post
        fields = [
            "id",
            "institute",
            "user_profile",
            "user_id",
            "user_name",
            "content",
            "category",
            "visibility",
            "media",
            "reaction_counts",
            "comments_count",
            "created_at",
            "updated_at",
        ]
    def get_user_profile(self, obj) -> str:
        return get_file_url(obj.user.profile, version=obj.user.cloudinary_version)

    @extend_schema_field(ReactionCountsSerializer)
    def get_reaction_counts(self, obj):
        cache_key = f"comment_reaction_counts_{obj.id}"

        data = cache.get(cache_key)
        if data is not None:
            return data

        counts = ReactionCount.objects.filter(
            target_type="post", target_id=obj.id
        ).first()

        if not counts:
            data = {
                "total": 0,
                "like": 0,
                "heart": 0,
                "haha": 0,
                "sad": 0,
                "angry": 0,
            }
        else:
            data = {
                "total": (
                    counts.like_count
                    + counts.haha_count
                    + counts.sad_count
                    + counts.heart_count
                    + counts.angry_count
                ),
                "like": counts.like_count,
                "heart": counts.heart_count,
                "haha": counts.haha_count,
                "sad": counts.sad_count,
                "angry": counts.angry_count,
            }

        # Cache for 5 minutes (tune as needed)
        cache.set(cache_key, data, timeout=300)

        return data

    def get_comments_count(self, obj) -> int:
        cache_key = f"post_comments_count_{obj.id}"

        count = cache.get(cache_key)

        if count is None:
            count = obj.comments.count()

            cache.set(cache_key, count, timeout=400)

        return count


class PostDetailSerializer(serializers.ModelSerializer):
    """
    Full version for a single post view.
    Includes full shared post details and any other heavy metadata.
    """

    media = PostMediaSerializer(many=True, source="postmedia_set", read_only=True)
    # Using FeedSerializer for shared_post keeps the nesting shallow but informative
    shared_post_details = PostFeedSerializer(source="shared_post", read_only=True)
    reaction_counts = serializers.SerializerMethodField()
    user_profile = serializers.SerializerMethodField()

    class Meta:
        model = Post
        fields = [
            "id",
            "institute",
            "user_id",
            "content",
            "category",
            "disable_comments",
            "visibility",
            "shared_post",
            "shared_post_details",
            "media",
            "reaction_counts",
            "user_profile",
        ]

    @extend_schema_field(PostDetailReactionCountsSerializer(allow_null=True))
    def get_reaction_counts(self, obj):
        # Reusing the same logic for consistency
        counts = ReactionCount.objects.filter(
            target_type="post", target_id=obj.id
        ).first()
        if not counts:
            return None
        return {
            "like": counts.like_count,
            "heart": counts.heart_count,
            "haha": counts.haha_count,
            "angry": counts.angry_count,
        }

    def get_user_profile(self, obj) -> str:
        return get_file_url(obj.user.profile, version=obj.user.cloudinary_version)


class PostCreateSerializer(serializers.ModelSerializer):
    uploaded_files = serializers.ListField(
        child=serializers.FileField(), write_only=True, required=False
    )

    class Meta:
        model = Post
        fields = [
            "content",
            "category",
            "visibility",
            "disable_comments",
            "shared_post",
            "uploaded_files",
        ]

    def create(self, validated_data):
        files = validated_data.pop("uploaded_files", [])
        post = Post.objects.create(**validated_data)

        if files:
            results = upload_files(files, folder=folder_for(post, Kind.POSTS), tags=tags_for(post))
            media_objs = [
                PostMedia(
                    post=post,
                    media_type=res["resource_type"],
                    media_url=res["public_id"],
                    cloudinary_version=res["version"],
                    order_index=i,
                )
                for i, res in enumerate(results)
            ]
            PostMedia.objects.bulk_create(media_objs)
        return post


class PostUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Post
        fields = ["content", "visibility", "disable_comments", "category"]

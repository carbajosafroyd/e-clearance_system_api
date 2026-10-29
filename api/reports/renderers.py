"""
Renderers that let a report view advertise binary download formats.

The download views return plain `HttpResponse`s, which skip renderers
entirely - so why are these here? Content negotiation runs *before* the
handler. With only the project's JSON renderer configured, a client that
asks for `Accept: application/pdf` (or the xlsx type) is turned away with
406 before any file is built. Listing the matching renderer on the view
makes that Accept header negotiable.

Each still has to render something in one case: when the request is rejected
(bad period, no permission) DRF returns a normal `Response`, and if the
client asked for the binary type then this is the renderer that was picked.
In that case it falls back to the project's usual JSON envelope, since an
error is not a document.
"""

from rest_framework.renderers import BaseRenderer

from api.utils.renderer import CustomResponseRenderer


class _BinaryDownloadRenderer(BaseRenderer):
    charset = None
    render_style = "binary"

    def render(self, data, accepted_media_type=None, renderer_context=None):
        if isinstance(data, (bytes, bytearray)):
            return data

        response = (renderer_context or {}).get("response")
        if response is not None:
            # Tell the truth about what is in the body.
            response["Content-Type"] = "application/json"

        return CustomResponseRenderer().render(
            data, "application/json", renderer_context
        )


class PdfRenderer(_BinaryDownloadRenderer):
    media_type = "application/pdf"
    format = "pdf"


class XlsxRenderer(_BinaryDownloadRenderer):
    media_type = (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    format = "xlsx"

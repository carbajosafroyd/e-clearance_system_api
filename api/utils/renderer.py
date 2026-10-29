from rest_framework.renderers import JSONRenderer
from rest_framework import status

class CustomResponseRenderer(JSONRenderer):
    def render(self, data, accepted_media_type=None, renderer_context=None):
        response = renderer_context.get("response", None)

        # Default values
        status_code = response.status_code if response else status.HTTP_200_OK
        message = "Success" if str(status_code).startswith("2") else "Error"
        errors = None
        result_data = data

        # DRF puts errors under "detail" or data if validation fails
        if not str(status_code).startswith("2"):
            errors = data
            result_data = None

        # Wrap response
        custom_response = {
            "status_code": status_code,
            "message": message,
            "data": result_data,
            "errors": errors,
        }

        return super().render(custom_response, accepted_media_type, renderer_context)

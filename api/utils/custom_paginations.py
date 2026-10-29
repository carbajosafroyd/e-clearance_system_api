from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response


class CustomPagination(PageNumberPagination):
    page_query_param = "current_page"
    page_size_query_param = "per_page"
    page_size = 10
    max_page_size = 100

    def paginate_queryset(self, queryset, request, view=None):
        self.request = request

        try:
            page_number = int(request.query_params.get(self.page_query_param, 1))
        except (TypeError, ValueError):
            page_number = 1

        paginator = self.django_paginator_class(queryset, self.get_page_size(request))
        total_pages = paginator.num_pages

        page_number = max(1, min(page_number, total_pages)) if total_pages > 0 else 1

        self.page = paginator.page(page_number)

        return list(self.page)

    def get_paginated_response(self, data):
        return Response(
            {
                "current_page": self.page.number,
                "per_page": self.get_page_size(self.request),
                "total_pages": self.page.paginator.num_pages,
                "total_items": self.page.paginator.count,
                "data": data,
            }
        )

from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

class CustomPagination(PageNumberPagination):
    page_query_param = "current_page"
    page_size_query_param = "per_page"
    max_page_size = 100  # optional

    def get_paginated_response(self, data):
        return Response({
            "current_page": self.page.number,
            "per_page": self.get_page_size(self.request),
            "total_pages": self.page.paginator.num_pages,
            "total_items": self.page.paginator.count,
            "results": data,
        })
        
        
"""
ENABLE IT HERE

REST_FRAMEWORK = {
    "DEFAULT_PAGINATION_CLASS": "api.examples.utils.CustomPagination",
    "PAGE_SIZE": 10,  # default per_page
}
"""


"""
Request:
GET /api/schools/?current_page=2&per_page=5

Response:
{
  "current_page": 2,
  "per_page": 5,
  "total_pages": 4,
  "total_items": 18,
  "results": [
    {
      "id": 6,
      "school_name": "ABC University",
      "logo": "https://example.com/logo.png",
      "location": {
        "id": 3,
        "city": "New York",
        "country": "USA"
      }
    },
    ...
  ]
}
"""

"""
If guso nimo e override

from .pagination import CustomPagination

class SchoolViewSet(viewsets.ModelViewSet):
    queryset = School.objects.all()
    pagination_class = CustomPagination  #applies only here

"""

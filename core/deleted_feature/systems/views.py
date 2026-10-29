# from django.shortcuts import render

# # Create your views here.

# from rest_framework import viewsets
# from rest_framework.permissions import IsAuthenticated

# from .models import System
# from api.systems.serializers import SystemSerializer
# from api.utils.custom_paginations import CustomPagination
# from rest_framework.filters import SearchFilter, OrderingFilter  


# from drf_spectacular.utils import extend_schema
# @extend_schema(tags=["System"])

# class SystemViewSet(viewsets.ModelViewSet):
#     queryset = System.objects.all().order_by("id")
#     pagination_class = CustomPagination # list actions use this
#     permission_classes = [IsAuthenticated]
#     serializer_class = SystemSerializer    
    
#     search_fields = ["name"]
#     ordering_fields = ["id"]
    
#     #forgot to addd serializers here :<


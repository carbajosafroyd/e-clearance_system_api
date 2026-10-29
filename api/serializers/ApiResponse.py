from rest_framework import serializers

class PaginatedDataSerializer(serializers.Serializer):
    current_page = serializers.IntegerField()
    per_page = serializers.IntegerField()
    total_pages = serializers.IntegerField()
    total_items = serializers.IntegerField()
    data = serializers.ListField() 

class ApiResponseSerializer(serializers.Serializer):
    status_code = serializers.IntegerField()
    message = serializers.CharField()
    data = PaginatedDataSerializer()
    errors = serializers.JSONField(allow_null=True)
    
class TokenResponseSerializer(serializers.Serializer):
    refresh = serializers.CharField()
    access = serializers.CharField()
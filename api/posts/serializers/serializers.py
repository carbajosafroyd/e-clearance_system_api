from rest_framework import serializers
from ..models import PostReaction

class PostReactionSerializer(serializers.ModelSerializer):
    user = serializers.StringRelatedField()  

    class Meta:
        model = PostReaction
        fields = ["id", "user", "reaction_type", "created_at"]
from django.core.cache import cache

from drf_spectacular.utils import extend_schema_field

from api.utils.cloudinary.cloudinary_utils import get_file_url

from ..models import Post, PostMedia, PostReaction, PostComment, ReactionCount
from .post_serializers import ReactionCountsSerializer
from rest_framework import serializers


class PostCommentListSerializer(serializers.ModelSerializer):
    user = serializers.StringRelatedField()
    user_profile = serializers.SerializerMethodField()
    reply_count = serializers.SerializerMethodField()
    reaction_counts = serializers.SerializerMethodField()

    class Meta:
        model = PostComment
        fields = [
            "id",
            "post",
            "user",
            "user_profile",
            "content",
            "created_at",
            "reply_count",
            "reaction_counts",
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
            target_type="comment", target_id=obj.id
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


    def get_reply_count(self, obj) -> int:
        cache_key = f"post_comment_replies_count_{obj.id}"

        count = cache.get(cache_key)

        if count is None:
            count = obj.replies.count()

            # Store in cache for 15 minutes (900s)
            cache.set(cache_key, count, timeout=900)
        return count


class PostCommentDetailSerializer(serializers.ModelSerializer):
    user = serializers.StringRelatedField()
    replies = serializers.SerializerMethodField()
    user_profile = serializers.SerializerMethodField()

    class Meta:
        model = PostComment
        fields = "__all__"

    @extend_schema_field(PostCommentListSerializer(many=True))
    def get_replies(self, obj):
        replies = obj.replies.all().order_by("created_at")
        return PostCommentListSerializer(replies, many=True).data

    def get_user_profile(self, obj) -> str:
        return get_file_url(obj.user.profile, version=obj.user.cloudinary_version)


class PostCommentCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = PostComment
        fields = ["post", "parent_comment", "content"]

    def validate(self, attrs):
        parent = attrs.get("parent_comment")
        post = attrs.get("post")

        if parent and parent.post_id != post.id:
            raise serializers.ValidationError(
                "Parent comment must belong to the same post."
            )

        return attrs


class PostCommentUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = PostComment
        fields = ["content"]

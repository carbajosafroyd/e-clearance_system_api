from rest_framework import viewsets, status
from rest_framework.response import Response
from rest_framework.decorators import action
from django.db import transaction
from api.posts.serializers.post_comments_serializer import (
    PostCommentListSerializer,
    PostCommentCreateSerializer,
    PostCommentDetailSerializer,
    PostCommentUpdateSerializer,
)
from api.utils.custom_paginations import CustomPagination
from rest_framework.permissions import IsAuthenticated

from api.rbac import ActionPermission, crud

from ..models import (
    Post,
    PostMedia,
    PostReaction,
    PostComment,
    ReactionCount,
    CommentReaction,
)

from drf_spectacular.utils import extend_schema, OpenApiExample
from drf_spectacular.types import OpenApiTypes
from rest_framework.parsers import JSONParser
from django.db import transaction

from django.db.models import F

from ..serializers.serializers import PostReactionSerializer

@extend_schema(tags=["Posts_Comments"])
class PostCommentViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, ActionPermission]
    required_permissions = crud(
        "posts",
        "postcomment",
        react="posts.view_postcomment",
        remove_react="posts.view_postcomment",
        reactions="posts.view_postcomment",
        replies="posts.view_postcomment",
    )
    queryset = (
        PostComment.objects.select_related("user", "post", "parent_comment")
        .all()
        .order_by("-created_at")
    )
    pagination_class = CustomPagination

    filterset_fields = ["post_id", "parent_comment_id", "user"]

    def get_queryset(self):
        queryset = super().get_queryset()
        post_id = self.request.query_params.get("post_id")
        parent_id = self.request.query_params.get("parent_comment_id")

        if post_id:
            queryset = queryset.filter(post_id=post_id)

        if parent_id:
            queryset = queryset.filter(parent_comment_id=parent_id)
        else:
            queryset = queryset.filter(
                parent_comment__isnull=True
            )  # top-level comments

        return queryset

    def get_serializer_class(self):
        if self.action == "list":
            return PostCommentListSerializer
        elif self.action == "retrieve":
            return PostCommentDetailSerializer
        elif self.action == "create":
            return PostCommentCreateSerializer
        elif self.action in ["update", "partial_update"]:
            return PostCommentUpdateSerializer
        return PostCommentDetailSerializer

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()

        instance.replies.all().delete()

        self.perform_destroy(instance)
        return Response(
            {"detail": "Comment deleted successfully."},
            status=status.HTTP_204_NO_CONTENT,
        )

    @extend_schema(
        request={
            "application/json": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": CommentReaction.CommentReactionType.values,
                        "description": "The type of reaction (e.g., like, heart, etc.)",
                    }
                },
                "required": ["type"],
            }
        },
        responses={200: OpenApiTypes.OBJECT},
        examples=[
            OpenApiExample(
                "Valid Reaction",
                value={"type": "heart"},
                request_only=True,
            ),
        ],
    )
    @action(
        detail=True,
        methods=["post"],
        parser_classes=[JSONParser],
    )
    def react(self, request, pk=None):
        comment = self.get_object()
        reaction_type = request.data.get("type")

        if reaction_type not in PostReaction.ReactionType.values:
            return Response(
                {"error": "Invalid reaction type"}, status=status.HTTP_400_BAD_REQUEST
            )

        print(comment)

        with transaction.atomic():

            # comment reaction
            existing_reaction = CommentReaction.objects.filter(
                post_comment=comment.id, user=request.user
            ).first()

            if existing_reaction and existing_reaction.reaction_type == reaction_type:
                return Response({"status": "unchanged", "current_type": reaction_type})

            counter, _ = ReactionCount.objects.get_or_create(
                target_type="comment", target_id=comment.id
            )

            # If user had previous reaction → decrement it
            if existing_reaction:
                old_field = f"{existing_reaction.reaction_type}_count"
                ReactionCount.objects.filter(id=counter.id).update(
                    **{old_field: F(old_field) - 1}
                )
                existing_reaction.reaction_type = reaction_type
                existing_reaction.save()
            else:
                # Create new reaction
                CommentReaction.objects.create(
                    post_comment=comment, user=request.user, reaction_type=reaction_type
                )

            new_field = f"{reaction_type}_count"
            ReactionCount.objects.filter(id=counter.id).update(
                **{new_field: F(new_field) + 1}
            )

        return Response({"status": "reaction recorded", "current_type": reaction_type})

    # @action(detail=True, methods=["get"])
    # def replies(self, request, pk=None):
    #     replies = PostComment.objects.filter(parent_comment_id=pk).order_by("-created_at")
    #     serializer = PostCommentListSerializer(replies, many=True)
    #     return Response(serializer.data)

    @action(detail=True, methods=["get"])
    def replies(self, request, pk=None):
        replies = PostComment.objects.filter(parent_comment_id=pk).order_by(
            "-created_at"
        )

        page = self.paginate_queryset(replies)

        if page is not None:
            serializer = PostCommentListSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = PostCommentListSerializer(replies, many=True)
        return Response(serializer.data)
    
    @action(detail=True, methods=["delete"])
    def remove_react(self, request, pk=None):
        comment = self.get_object()

        with transaction.atomic():
            reaction = CommentReaction.objects.filter(post_comment=comment, user=request.user).first()

            if not reaction:
                return Response({"status": "no reaction"}, status=404)

            counter, _ = ReactionCount.objects.get_or_create(
                target_type="comment", target_id=comment.id
            )

            field = f"{reaction.reaction_type}_count"

            ReactionCount.objects.filter(id=counter.id).update(**{field: F(field) - 1})

            reaction.delete()

        return Response({"status": "removed"})

    @action(detail=True, methods=["get"])
    def reactions(self, request, pk=None):
        comment = self.get_object()

        queryset = CommentReaction.objects.filter(post_comment=comment).select_related("user")

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = PostReactionSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = PostReactionSerializer(queryset, many=True)
        return Response(serializer.data)


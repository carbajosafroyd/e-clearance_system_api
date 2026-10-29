from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser
from django.db.models import F
from drf_spectacular.utils import extend_schema, OpenApiExample
from drf_spectacular.types import OpenApiTypes
from rest_framework.parsers import JSONParser
from django.db import transaction

from api.utils.custom_paginations import CustomPagination

from ..models import Post, PostReaction, ReactionCount
from ..serializers.post_serializers import (
    PostFeedSerializer,
    PostDetailSerializer,
    PostCreateSerializer,
    PostUpdateSerializer,
)
from ..serializers.serializers import PostReactionSerializer

from api.utils.cloudinary.cloudinary_utils import delete_file, delete_files
from rest_framework.permissions import IsAuthenticated

from api.rbac import ActionPermission, crud


@extend_schema(tags=["Posts"])
class PostViewSet(viewsets.ModelViewSet):
    # The feed serializer renders each post's media list, and the detail view
    # re-renders the shared post through the feed serializer (its user,
    # institute and media included).
    queryset = (
        Post.objects.all()
        .select_related("user", "institute", "shared_post__user", "shared_post__institute")
        .prefetch_related("postmedia_set", "shared_post__postmedia_set")
    )
    permission_classes = [IsAuthenticated, ActionPermission]
    required_permissions = crud(
        "posts",
        "post",
        # Reacting is not editing the post - anyone who can read the feed
        # may react to it.
        react="posts.view_post",
        remove_react="posts.view_post",
        reactions="posts.view_post",
        toggle_comments="posts.change_post",
    )
    parser_classes = [MultiPartParser, FormParser]
    pagination_class = CustomPagination

    def get_serializer_class(self): # type: ignore
        if self.action == "list":
            return PostFeedSerializer
        if self.action == "retrieve":
            return PostDetailSerializer
        if self.action == "create":
            return PostCreateSerializer
        if self.action in ["update", "partial_update"]:
            return PostUpdateSerializer
        return PostDetailSerializer

    def perform_create(self, serializer):
        user = self.request.user
        serializer.save(user=user, institute=user.institute)

    def perform_destroy(self, instance):
        media_files = instance.postmedia_set.all()

        files_to_delete = []
        for media in media_files:
            files_to_delete.append(
                {
                    "public_id": media.media_url,
                    "resource_type": media.media_type,
                    "version": media.cloudinary_version,
                }
            )

        if files_to_delete:
            delete_files(files_to_delete)

        super().perform_destroy(instance)

    @extend_schema(
        request={
            "application/json": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": PostReaction.ReactionType.values,
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
        post = self.get_object()
        reaction_type = request.data.get("type")

        if reaction_type not in PostReaction.ReactionType.values:
            return Response(
                {"error": "Invalid reaction type"}, status=status.HTTP_400_BAD_REQUEST
            )
        print(post)
        with transaction.atomic():

            existing_reaction = PostReaction.objects.filter(
                post=post, user=request.user
            ).first()
            
            print(existing_reaction)

            if existing_reaction and existing_reaction.reaction_type == reaction_type:
                return Response({"status": "unchanged", "current_type": reaction_type})
            counter, _ = ReactionCount.objects.get_or_create(
                target_type="post", target_id=post.id
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
                PostReaction.objects.create(
                    post=post, user=request.user, reaction_type=reaction_type
                )

            new_field = f"{reaction_type}_count"
            ReactionCount.objects.filter(id=counter.id).update(
                **{new_field: F(new_field) + 1}
            )

        return Response({"status": "reaction recorded", "current_type": reaction_type})

    @action(detail=True, methods=["delete"])
    def remove_react(self, request, pk=None):
        post = self.get_object()

        with transaction.atomic():
            reaction = PostReaction.objects.filter(post=post, user=request.user).first()

            if not reaction:
                return Response({"status": "no reaction"}, status=404)
        
            counter, _ = ReactionCount.objects.get_or_create(
                target_type="post", target_id=post.id
            )

            field = f"{reaction.reaction_type}_count"

            ReactionCount.objects.filter(id=counter.id).update(**{field: F(field) - 1})

            reaction.delete()

        return Response({"status": "removed"})

    @action(detail=True, methods=["get"])
    def reactions(self, request, pk=None):
        post = self.get_object()

        queryset = PostReaction.objects.filter(post=post).select_related("user")

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = PostReactionSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = PostReactionSerializer(queryset, many=True)
        return Response(serializer.data)

    @extend_schema(request={"disable_comments": bool})
    @action(detail=True, methods=["patch"])
    def toggle_comments(self, request, pk=None):
        post = self.get_object()

        # Extract the value from the request, default to current state if not provided
        new_state = request.data.get("disable_comments", not post.disable_comments)

        post.disable_comments = new_state
        post.save()

        return Response({"disable_comments": post.disable_comments})

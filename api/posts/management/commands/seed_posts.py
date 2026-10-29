"""Seed a short announcement feed with comments and reactions."""

from __future__ import annotations

from api.institutes.models import Institute
from api.posts.models import Post, PostComment, PostReaction, ReactionCount
from api.rbac.roles import Roles
from api.seeding.support import SeedCommand
from api.users.models import User

POSTS = [
    (
        Post.Category.ANNOUNCEMENT,
        "Collection for the second semester opens on Monday. Settle your "
        "balances at the collection office or through GCash.",
    ),
    (
        Post.Category.REMINDER,
        "Overdue fees are now flagged in your dashboard. Talk to the "
        "collection office if you need an arrangement.",
    ),
    (
        Post.Category.EVENT,
        "Foundation Week attendance is being recorded. Missing a slot adds "
        "an attendance fee to your account.",
    ),
]

COMMENTS = [
    "Noted, thank you!",
    "Is GCash payment still accepted after the due date?",
    "Where is the collection office located?",
]


class Command(SeedCommand):
    seed_name = "posts"
    help = "Create a short demo announcement feed."
    depends_on = ("seed_users", "seed_institutes")

    def reset(self, **options):
        deleted, _ = Post.objects.all().delete()
        self.warn(f"deleted {deleted} post row(s) and their comments")

    def seed(self, **options):
        institutes = self.require(Institute, "seed_institutes")

        created_posts = 0
        created_comments = 0
        created_reactions = 0

        for institute in institutes:
            author = (
                User.objects.filter(
                    institute=institute,
                    groups__name__in=[Roles.CMS_ADMIN, Roles.SCHOOL_ADMIN],
                )
                .order_by("id")
                .first()
            )
            if author is None:
                self.warn(
                    f"{institute.institute_name}: no admin account, skipping posts"
                )
                continue

            readers = list(
                User.objects.filter(
                    institute=institute, groups__name=Roles.STUDENT
                ).order_by("id")[:10]
            )

            for category, content in POSTS[: max(1, min(len(POSTS), self.scale + 2))]:
                post, made = Post.objects.get_or_create(
                    institute=institute,
                    user=author,
                    content=content,
                    defaults={"category": category},
                )
                created_posts += int(made)

                if not made or not readers:
                    continue

                for reader in self.sample(readers, min(3, len(readers))):
                    PostComment.objects.create(
                        post=post, user=reader, content=self.pick(COMMENTS)
                    )
                    created_comments += 1

                reactors = self.sample(readers, min(5, len(readers)))
                for reader in reactors:
                    _, reacted = PostReaction.objects.get_or_create(
                        post=post,
                        user=reader,
                        defaults={
                            "reaction_type": self.pick(
                                [
                                    PostReaction.ReactionType.LIKE,
                                    PostReaction.ReactionType.HEART,
                                ]
                            )
                        },
                    )
                    created_reactions += int(reacted)

                # ReactionCount is a denormalised counter keyed by
                # target_type/target_id, not a relation - the feed reads it,
                # so a seeded post needs its row.
                counts = {
                    "like_count": sum(
                        1
                        for reaction in PostReaction.objects.filter(post=post)
                        if reaction.reaction_type == PostReaction.ReactionType.LIKE
                    ),
                    "heart_count": sum(
                        1
                        for reaction in PostReaction.objects.filter(post=post)
                        if reaction.reaction_type == PostReaction.ReactionType.HEART
                    ),
                }
                ReactionCount.objects.update_or_create(
                    target_type=ReactionCount.TargetType.POST,
                    target_id=post.pk,
                    defaults=counts,
                )

        self.created("post", created_posts)
        self.created("comment", created_comments)
        self.created("reaction", created_reactions)

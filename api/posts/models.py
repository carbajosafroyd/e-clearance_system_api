from django.db import models
from django.conf import settings

from api.institutes.models import Institute
from core.BaseModel import TimeStampedModel

User = settings.AUTH_USER_MODEL

from django.core.cache import cache


class Post(TimeStampedModel):
    class Visibility(models.TextChoices):
        PUBLIC = "public", "Public"
        FOLLOWERS = "followers", "Followers"
        PRIVATE = "private", "Private"

    class Category(models.TextChoices):
        GENERAL = "general", "General"
        ANNOUNCEMENT = "announcement", "Announcement"
        AWARDING = "awarding", "Awarding"
        EVENT = "event", "Event"
        REMINDER = "reminder", "Reminder"

    institute = models.ForeignKey(
        Institute,
        on_delete=models.CASCADE,
        related_name="institutes",
        null=False,
        blank=False,
    )

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="posts")
    content = models.TextField(blank=True, null=True)
    disable_comments = models.BooleanField(default=False)
    
    visibility = models.CharField(
        max_length=20, choices=Visibility.choices, default=Visibility.PUBLIC
    )
    
    category = models.CharField(
        max_length=20, choices=Category.choices, default=Category.GENERAL
    )
    
    shared_post = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="shared_by",
    )

    class Meta:
        db_table = "posts"

    def __str__(self):
        return f"Post {self.id} ({self.category}) by User {self.user_id}"


class PostMedia(TimeStampedModel):
    class MediaType(models.TextChoices):
        IMAGE = "image", "Image"
        VIDEO = "video", "Video"

    post = models.ForeignKey(
        Post, on_delete=models.CASCADE, related_name="postmedia_set"
    )

    media_type = models.CharField(max_length=10, choices=MediaType.choices)

    media_url = models.CharField(max_length=100)  # public id ni if cloudinary
    #: The Cloudinary account version this media was uploaded under - see
    #: CLOUDINARY_ACCOUNTS in settings. Null means "not known yet": the
    #: helpers look it up across accounts on first use.
    cloudinary_version = models.PositiveSmallIntegerField(null=True, blank=True)
    order_index = models.IntegerField(default=0)

    class Meta:
        db_table = "post_medias"
        ordering = ["order_index"]

    def __str__(self):
        return f"{self.media_type} for Post {self.post_id}"


class PostComment(TimeStampedModel):
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name="comments")
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="post_comments"
    )

    parent_comment = models.ForeignKey(
        "self", on_delete=models.CASCADE, null=True, blank=True, related_name="replies"
    )

    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "post_comments"

    def __str__(self):
        return f"Comment {self.id} on Post {self.post_id}"

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        super().save(*args, **kwargs)
        if is_new:
            cache.delete(f"post_comments_count_{self.post_id}")

    def delete(self, *args, **kwargs):
        post_id = self.post_id
        super().delete(*args, **kwargs)
        cache.delete(f"post_comments_count_{post_id}")


class PostReaction(TimeStampedModel):
    class ReactionType(models.TextChoices):
        LIKE = "like", "Like"
        HEART = "heart", "Heart"
        HAHA = "haha", "Haha"
        SAD = "sad", "Sad"
        ANGRY = "angry", "Angry"

    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name="reactions")
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="post_reactions"
    )
    reaction_type = models.CharField(max_length=10, choices=ReactionType.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "post_reactions"
        # Prevent a user from reacting to the exact same post multiple times
        unique_together = ("post", "user")

    def __str__(self):
        return f"{self.reaction_type} by User {self.user_id} on Post {self.post_id}"
    
class CommentReaction(TimeStampedModel):
    class CommentReactionType(models.TextChoices):
        LIKE = "like", "Like"
        HEART = "heart", "Heart"
        HAHA = "haha", "Haha"
        SAD = "sad", "Sad"
        ANGRY = "angry", "Angry"

    post_comment = models.ForeignKey(
        PostComment, on_delete=models.CASCADE, related_name="comment_reactions"
    )
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="post_comment_reactions"
    )
    reaction_type = models.CharField(max_length=10, choices=CommentReactionType.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "post_comments_reactions"
        # Prevent a user from reacting to the exact same post multiple times
        unique_together = ("post_comment", "user")

    def __str__(self):
        return f"{self.reaction_type} by User {self.user_id} on Comment {self.post_comment}"
    

    def save(self, *args, **kwargs):
        is_new = self.pk is None

        old_reaction = None
        if not is_new:
            old_reaction = CommentReaction.objects.get(pk=self.pk).reaction_type
class ReactionCount(TimeStampedModel):
    class TargetType(models.TextChoices):
        POST = "post", "Post"
        COMMENT = "comment", "Comment"

    # Instead of GenericForeignKeys, tracking by type/id strings is often faster
    # for simple denormalization tables like this.
    target_type = models.CharField(max_length=10, choices=TargetType.choices)
    target_id = models.IntegerField()

    like_count = models.PositiveIntegerField(default=0)
    heart_count = models.PositiveIntegerField(default=0)
    haha_count = models.PositiveIntegerField(default=0)
    sad_count = models.PositiveIntegerField(default=0)
    angry_count = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "post_reaction_counts"
        # Ensure we only have one counter row per target item
        unique_together = ("target_type", "target_id")

    def __str__(self):
        return f"Counts for {self.target_type} {self.target_id}"

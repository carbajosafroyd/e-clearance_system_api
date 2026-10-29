# from django.db import models
# from api.utils.core.BaseModel import BaseModel

# class UserSystem(BaseModel): 
#     user = models.ForeignKey( 
#         "users.User", on_delete=models.CASCADE, related_name="user_systems"
#     )
#     system = models.ForeignKey(
#         "systems.System", on_delete=models.CASCADE, related_name="system_users"
#     )
    
#     class Meta:
#         db_table = "user_systems"

#     def __str__(self):
#         return f"User: {self.user.username}, System: {self.system.name}"
    
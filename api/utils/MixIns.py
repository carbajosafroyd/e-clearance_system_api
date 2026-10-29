class UserContextMixin:
    @property
    def user(self):
        return self.request.user

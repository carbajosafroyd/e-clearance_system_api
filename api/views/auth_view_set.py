from tokenize import TokenError

from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from api.serializers.AuthSerializer import CustomTokenObtainPairSerializer
from api.serializers.AuthUserSerializer import AuthUserSerializer
from rest_framework_simplejwt.views import TokenObtainPairView

from rest_framework.views import APIView
from rest_framework import status
from rest_framework_simplejwt.tokens import RefreshToken
from api.serializers.AuthSerializer import LogoutSerializer

# from api.utils.token_extractor import get_user_from_token


from drf_spectacular.utils import extend_schema

from api.users.serializers import UserUpdateSerializer
from api.users.throttles import LoginIPThrottle, LoginPerAccountThrottle

from django.core.cache import cache

from rest_framework.decorators import action

@extend_schema(tags=["Current User"])
class AuthViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = AuthUserSerializer

    def list(self, request):
        # cache_key = f"user_me_{request.user.id}"

        # cached_data = cache.get(cache_key)
        # if cached_data:
        #     return Response(cached_data)

        # groups / permissions / is_superuser / is_staff are part of the
        # serializer now - see AuthUserSerializer - so the schema describes
        # the whole payload and the frontend's types stay in step.
        serializer = self.serializer_class(request.user)
        data = serializer.data

        # cache.set(cache_key, data, timeout=60 * 20)  # 20 minutes
        return Response(data)

        # user, exp, groups, institute, school, systems = get_user_from_token(request)  # , groups, permissions

        # if not user:
        #     return Response({"error": "Invalid or missing token"}, status=401)

        # data = {
        #     "username": user.username,
        #     "email": user.email,
        #     "id": user.id,
        #     "exp": exp,
        #     "groups": groups,
        #     "institute": institute,
        #     "school": school,
        #     "systems": systems,
        #     # "permissions": permissions
        # }

        # return Response(data)
        
    @extend_schema(request=UserUpdateSerializer, responses=UserUpdateSerializer)
    @action(detail=False, methods=["patch"], url_path="update")
    def update_me(self, request):
        serializer = UserUpdateSerializer(
            request.user,
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)
    


class CustomTokenObtainPairView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer
    throttle_classes = [
        LoginPerAccountThrottle,
        LoginIPThrottle,
    ]


@extend_schema(request=LogoutSerializer)
class LogoutView(APIView):
    permission_classes = [IsAuthenticated]
    serializer_class = LogoutSerializer

    def post(self, request):
        try:
            serializer = LogoutSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            serializer.save()

            return Response(
                {"detail": "Successfully logged out."},
                status=status.HTTP_205_RESET_CONTENT,
            )   
        except Exception as e:
            return Response(
                {"error": "This refresh token is invalid or already blacklisted."},
                status=status.HTTP_400_BAD_REQUEST,
            )

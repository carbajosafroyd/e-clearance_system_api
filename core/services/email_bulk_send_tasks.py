"""
NOT WIRED UP - a sketch of the Celery path, kept for whenever this project
grows a broker. Nothing imports it.

Heads up before you switch to it: the context below is the OLD invitation
context. The live path (api/users/views.py -> _invite_context) also passes
the school branding, the reserved username and the one-time password, and
the template needs all three. Copy that helper rather than this file, or
invited users will get an unbranded email they cannot sign in with.
"""

from celery import shared_task
from django.core import mail
from django.conf import settings
from django.template.loader import render_to_string
from datetime import datetime

from core.services.links import backend_link

@shared_task(bind=True, max_retries=3)
def send_bulk_invites_task(self, invite_data_list):
    """
    invite_data_list: list of dicts [{'email': '...', 'token': '...', 'group_name': '...'}]
    """
    connection = mail.get_connection()
    messages = []
    current_year = datetime.now().year

    for data in invite_data_list:
        registration_link = backend_link("/api/v1/register/", token=data["token"])
        
        context = {
            "link": registration_link,
            "group_name": data['group_name'],
            "year": current_year,
        }

        html_content = render_to_string("email_templates/user_invite.html", context)
        text_content = render_to_string("email_templates/user_invite.txt", context)

        email = mail.EmailMultiAlternatives(
            subject="Complete your account registration",
            body=text_content,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[data['email']],
        )
        email.attach_alternative(html_content, "text/html")
        messages.append(email)

    try:
        connection.open()
        connection.send_messages(messages)
        connection.close()
    except Exception as exc:
        # If the SMTP server fails, retry in 5 minutes
        raise self.retry(exc=exc, countdown=300)

"""
HOW TO USE. 

in view
def post(self, request):
    serializer = BulkUserInviteSerializer(data=request.data, context={"request": request})
    serializer.is_valid(raise_exception=True)
    
    # 1. Save to DB first
    tokens = serializer.save()

    # 2. Extract only the simple data needed for the task
    # (Don't pass the actual 'tokens' queryset/objects to Celery)
    invite_payload = [
        {
            "email": t.email, 
            "token": t.token, 
            "group_name": t.group.name
        } for t in tokens
    ]

    # 3. Offload to Celery
    send_bulk_invites_task.delay(invite_payload)

    return Response(
        {"detail": f"{len(tokens)} invitations are being processed."},
        status=201,
    )

############
install redis
###########

pip install redis

############
update settings.py 
############
CELERY_BROKER_URL = 'redis://localhost:6379/0'
CELERY_RESULT_BACKEND = 'redis://localhost:6379/0'
"""
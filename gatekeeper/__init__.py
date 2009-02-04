
__author__ = "Jeremy Carbaugh (jcarbaugh@sunlightfoundation.com)"
__version__ = "0.1"
__copyright__ = "Copyright (c) 2008 Sunlight Labs"
__license__ = "BSD"

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.core.mail import send_mail
from django.core.urlresolvers import reverse
from django.db.models import Model, signals
from django.dispatch import Signal
from gatekeeper.middleware import get_current_user
from gatekeeper.models import ModeratedObject
import datetime

ENABLE_AUTOMODERATION = getattr(settings, "GATEKEEPER_ENABLE_AUTOMODERATION", False)
DEFAULT_STATUS = getattr(settings, "GATEKEEPER_DEFAULT_STATUS", 0)
MODERATOR_LIST = getattr(settings, "GATEKEEPER_MODERATOR_LIST", [])

post_moderation = Signal(providing_args=["instance"])
post_flag = Signal(providing_args=["instance"])

def _get_automod_user():
    try:
        return User.objects.get(username__exact="gatekeeper_automod")
    except User.DoesNotExist:
        from django.contrib.sites.models import Site
        site = Site.objects.get(id=settings.SITE_ID)
        automod_user = User.objects.create_user(
            'gatekeeper_automod', 'gatekeeper_automod@%s' % site.domain)
        automod_user.save()
        return automod_user

#
# register models with gatekeeper
#
# 

registered_models = {}

def register(model, import_unmoderated=False, auto_moderator=None):
    if not model in registered_models:
        signals.post_save.connect(save_handler, sender=model)
        signals.pre_delete.connect(delete_handler, sender=model)
        registered_models[model] = auto_moderator
        if import_unmoderated:
            try:
                unmod_objs = unmoderated(model.objects.all())
                for obj in unmod_objs:
                    mo = ModeratedObject(
                        moderation_status=DEFAULT_STATUS,
                        content_object=obj,
                        timestamp=datetime.datetime.now())
                    mo.save()
            except:
                pass
                
#
# handler for object creation/deletion
#

def save_handler(sender, **kwargs):

    if kwargs.get('created', None):
    
        instance = kwargs['instance']
    
        mo = ModeratedObject(
            moderation_status=DEFAULT_STATUS,
            content_object=instance,
            timestamp=datetime.datetime.now())
        mo.save()
    
        if ENABLE_AUTOMODERATION:
        
            auto_moderator = registered_models[instance.__class__]
            if auto_moderator:
                mod = auto_moderator(mo)
                if mod is None:
                    pass # ignore the moderator if it returns None
                elif mod:
                    mo.approve(_get_automod_user())
                else:
                    mo.reject(_get_automod_user())
        
            if mo.moderation_status == 0: # if status is still pending
                user = get_current_user()
                if user and user.is_authenticated():
                    if user.is_superuser or user.has_perm('gatekeeper.change_moderatedobject'):
                        mo.approve(user)
                    
        if MODERATOR_LIST:
            subject = "[pending-moderation] %s" % instance
            message = "New object pending moderation.\n%s\n%s" % (instance, reverse("gatekeeper_moderate_list"))
            from_addr = "bounce@sunlightfoundation.com"
            send_mail(subject, message, from_addr, MODERATOR_LIST, fail_silently=True)

def delete_handler(sender, **kwargs):
    instance = kwargs['instance']
    try:
        ct = ContentType.objects.get_for_model(sender)
        mo = ModeratedObject.objects.get(content_type=ct, object_id=instance.pk)
        mo.delete()
    except ModeratedObject.DoesNotExist:
        pass

#
# filter querysets or test objects
#

def approved(qs_or_obj):
    return _by_status(1, qs_or_obj)

def pending(qs_or_obj):
    return _by_status(0, qs_or_obj)

def rejected(qs_or_obj):
    return _by_status(-1, qs_or_obj)

def unmoderated(qs_or_obj):
    return _by_status(None, qs_or_obj)

def _by_status(status, qs_or_obj):
    if hasattr(qs_or_obj, "model"):
        # filter queryset
        ct = ContentType.objects.get_for_model(qs_or_obj.model)
        if status is None:
            # filter unmoderated instances
            ids = ModeratedObject.objects.filter(content_type=ct).values_list('object_id', flat=True)
            qs = qs_or_obj.exclude(pk__in=ids)
        else:
            # filter approved, pending, and rejected instances
            ids = ModeratedObject.objects.filter(content_type=ct, moderation_status=status).values_list('object_id', flat=True)
            qs = qs_or_obj.filter(pk__in=ids)
        return qs
    elif isinstance(qs_or_obj, Model):
        # filter single model instance
        ct = ContentType.objects.get_for_model(qs_or_obj.__class__)
        try:
            # test for approved, pending, or rejected status
            mo = ModeratedObject.objects.get(content_type=ct, object_id=qs_or_obj.pk)
            if mo.moderation_status == status:
                return qs_or_obj
        except ModeratedObject.DoesNotExist:
            # test for unmoderated status
            if status is None:
                return qs_or_obj
    else:
        raise ValueError('object must be a queryset or model instance')


def flagged(qs_or_obj):
    if hasattr(qs_or_obj, 'model'):
        # filter queryset
        ct = ContentType.objects.get_for_model(qs_or_obj.model)
        flagged_ids = ModeratedObject.objects.filter(content_type=ct, flagged=True).values_list('object_id', flat=True)


from django import template
from django import template
from datetime import timedelta

register = template.Library()

@register.filter
def add_days(date, days):
    """Add days to a date"""
    try:
        return date + timedelta(days=int(days))
    except (ValueError, TypeError):
        return date


@register.filter(name='has_group')
def has_group(user, group_name):
    """Check if user belongs to a group"""
    return user.groups.filter(name=group_name).exists()
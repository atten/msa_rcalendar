from django.conf.urls import url, include

urlpatterns = [
    url(r'^api/v1/', include('rcalendar.urls')),
]

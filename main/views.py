from django.shortcuts import render

# Create your views here.

from django.shortcuts import render

def homepage_view(request):
    return render(request, 'homepage.html')

def paperlist_view(request):
    return render(request, 'paperlist.html')

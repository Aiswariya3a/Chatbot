from django.contrib import admin
from .models import Patient, Appointment

@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    list_display = ('name', 'age', 'gender', 'medical_history')

@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    list_display = ('patient', 'package_name', 'hospital_name', 'appointment_date', 'appointment_time', 'reference_number', 'status', 'is_recurring')
    list_filter = ('status', 'appointment_date', 'hospital_name')
    search_fields = ('patient__name', 'reference_number')

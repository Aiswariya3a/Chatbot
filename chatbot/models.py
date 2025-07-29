from django.db import models

class Patient(models.Model):
    name = models.CharField(max_length=255)
    age = models.IntegerField()
    gender = models.CharField(max_length=10) # e.g., 'male', 'female', 'other'
    medical_history = models.TextField(blank=True, null=True) # JSONField could be better for structured history

    def __str__(self):
        return self.name

class Appointment(models.Model):
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE)
    package_id = models.CharField(max_length=50) # From CSV
    package_name = models.CharField(max_length=255) # From CSV
    hospital_name = models.CharField(max_length=255) # From CSV
    appointment_date = models.DateField()
    appointment_time = models.CharField(max_length=50) # Or TimeField
    reference_number = models.CharField(max_length=100, unique=True)
    status = models.CharField(max_length=50, default='confirmed') # e.g., 'confirmed', 'cancelled'
    is_recurring = models.BooleanField(default=False)
    recurrence_interval = models.CharField(max_length=50, blank=True, null=True) # e.g., '6 months', '1 year'
    
    def __str__(self):
        return f"Appointment for {self.patient.name} - {self.package_name} on {self.appointment_date}"

# Create your models here.

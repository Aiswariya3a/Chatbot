import google.generativeai as genai
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
import json
import re
from datetime import datetime, timedelta
import uuid # For generating unique reference numbers
from django.shortcuts import render

import pandas as pd

from .utils import load_checkups_data
from .models import Patient, Appointment # If you decide to use models

# Configure Gemini API
genai.configure(api_key=settings.GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')

# In-memory session for demonstration (replace with Django sessions or database for production)
user_sessions = {}

@csrf_exempt
def chatbot_api(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            user_message = data.get("message", "").strip()
            session_id = request.session.session_key
            if not session_id:
                request.session.save()
                session_id = request.session.session_key

            if session_id not in user_sessions:
                user_sessions[session_id] = {"state": "initial", "patient_data": {}}

            session = user_sessions[session_id]

            bot_reply = process_user_message(user_message, session)
            return JsonResponse({"reply": bot_reply})
        except json.JSONDecodeError:
            return JsonResponse({"reply": "Invalid JSON format."}, status=400)
        except Exception as e:
            return JsonResponse({"reply": f"An error occurred: {str(e)}"}, status=500)
    return JsonResponse({"reply": "Method not allowed."}, status=405)


def process_user_message(message, session):
    state = session.get("state", "initial")
    patient_data = session.get("patient_data", {})
    checkups_df = load_checkups_data()
    
     # Complex Use Case: Recurring Checkups
    if "follow-up" in message.lower() or "recurring" in message.lower():
        # This would require more sophisticated NLP to extract recurrence interval
        prompt = f"Extract the follow-up interval (e.g., 6 months, 1 year) from: '{message}'"
        gemini_response = model.generate_content(prompt).text.strip()
        interval_match = re.search(r"(\d+)\s*(month|year)s?", gemini_response, re.IGNORECASE)

        if interval_match:
            num = int(interval_match.group(1))
            unit = interval_match.group(2)
            current_date = datetime.now().date() # Or last appointment date
            if unit == 'month':
                follow_up_date = current_date + timedelta(days=num * 30) # Approximate
            else: # year
                follow_up_date = current_date + timedelta(days=num * 365) # Approximate

            patient_data["preferred_date"] = follow_up_date
            patient_data["is_recurring"] = True
            patient_data["recurrence_interval"] = f"{num} {unit}s"
            session["patient_data"] = patient_data
            session["state"] = "confirm_slot" # Re-use slot confirmation flow
            return f"For a follow-up on {follow_up_date.strftime('%Y-%m-%d')}, I'll check availability. " + display_available_slots(patient_data, checkups_df, session)
        else:
            return "For recurring checkups, please specify the interval (e.g., 'in 6 months', 'annually')."

    if state == "initial":
        # ... (initial state logic)
        if "schedule" in message.lower():
            session["state"] = "collect_details"
            return "Please provide your name, age, gender, and any medical history (e.g., Jane Doe, 45, female, history of hypertension)."
        elif "packages" in message.lower() or "list" in message.lower():
            session["state"] = "initial" # Reset state
            return display_available_packages(checkups_df)
        else:
            return "Welcome to the Health Checkup Scheduling Bot! Do you want to schedule a checkup or view available packages?"

    elif state == "collect_details":
        prompt = (
            f"From the following text, extract the Name, Age, Gender, and Medical History. "
            f"Format your output as a markdown list, like: '* **Name:** [name]\\n* **Age:** [age]...'. "
            f"If any piece of information is missing, use 'N/A' for that specific field.\n"
            f"Text: '{message}'"
        )
        gemini_response = model.generate_content(prompt).text.strip()
        print(f"Gemini Raw Response: {gemini_response}") # Keep for continued debugging

        # --- UPDATED REGEX PATTERNS TO MATCH MARKDOWN LIST ---
        name_match = re.search(r"\* \*\*Name:\*\* (.*?)(?:\n|$)", gemini_response, re.IGNORECASE)
        age_match = re.search(r"\* \*\*Age:\*\* (\d+)(?:\n|$)", gemini_response, re.IGNORECASE)
        gender_match = re.search(r"\* \*\*Gender:\*\* (.*?)(?:\n|$)", gemini_response, re.IGNORECASE)
        medical_history_match = re.search(r"\* \*\*Medical History:\*\* (.*)", gemini_response, re.IGNORECASE)

        name = name_match.group(1).strip() if name_match else None
        age = int(age_match.group(1)) if age_match and age_match.group(1).isdigit() else None
        gender = gender_match.group(1).strip() if gender_match else None
        medical_history = medical_history_match.group(1).strip() if medical_history_match else ""

        # Handle "N/A" cases from Gemini's response
        if name and name.lower() == 'n/a': name = None
        if gender and gender.lower() == 'n/a': gender = None
        if medical_history and medical_history.lower() == 'n/a': medical_history = ""


        if name and age and gender:
            patient_data["name"] = name
            patient_data["age"] = age
            patient_data["gender"] = gender
            patient_data["medical_history"] = medical_history
            session["patient_data"] = patient_data
            session["state"] = "recommend_package"
            return recommend_checkup_package(patient_data, checkups_df) + " Preferred date? (YYYY-MM-DD)"
        else:
            missing_info = []
            if not name: missing_info.append("name")
            if not age: missing_info.append("age")
            if not gender: missing_info.append("gender")

            if missing_info:
                return f"I couldn't get your {', '.join(missing_info)}. Please provide your name, age, gender, and any medical history (e.g., Jane Doe, 45, female, history of hypertension)."
            else:
                return "I couldn't process your details. Please provide your name, age, gender, and any medical history (e.g., Jane Doe, 45, female, history of hypertension)."

    elif state == "recommend_package":
        try:
            preferred_date = datetime.strptime(message, "%Y-%m-%d").date()
            patient_data["preferred_date"] = preferred_date
            session["patient_data"] = patient_data
            session["state"] = "confirm_slot"
            return display_available_slots(patient_data, checkups_df, session)
        except ValueError:
            return "Invalid date format. Please use YYYY-MM-DD."
        
    elif state == "select_alternative_slot":
        alternatives = session.get("alternative_slots", [])
        selected_alternative = None

        num_match = re.match(r'^\s*(\d+)\s*$', message)
        if num_match:
            index = int(num_match.group(1)) - 1
            if 0 <= index < len(alternatives):
                selected_alternative = alternatives[index]
        else:
            # Try to parse by text (hospital name, date part, time part)
            # Use Gemini to extract the selected alternative details
            prompt = (
                f"From the alternatives provided: {', '.join([str(alt) for alt in alternatives])}\n"
                f"Identify the hospital, date (YYYY-MM-DD), and time (HH:MM IST) selected in the user's message: '{message}'. "
                f"Format as: Hospital: [hospital_name], Date: [date], Time: [time]. If uncertain, state N/A."
            )
            gemini_response = model.generate_content(prompt).text.strip()
            print(f"Gemini Alternative Selection Response: {gemini_response}") # Debug Gemini's output

            # Regex to parse Gemini's structured response for selection
            hospital_match = re.search(r"Hospital:\s*(.*?)(?:,|$)", gemini_response, re.IGNORECASE)
            date_match = re.search(r"Date:\s*(\d{4}-\d{2}-\d{2})", gemini_response, re.IGNORECASE)
            time_match = re.search(r"Time:\s*(\d{2}:\d{2}\s*IST)", gemini_response, re.IGNORECASE)

            extracted_hospital = hospital_match.group(1).strip() if hospital_match else None
            extracted_date_str = date_match.group(1).strip() if date_match else None
            extracted_time = time_match.group(1).strip() if time_match else None

            # Attempt to match extracted details to one of the stored alternatives
            if extracted_hospital and extracted_date_str and extracted_time:
                try:
                    extracted_date = datetime.strptime(extracted_date_str, '%Y-%m-%d').date()
                    for alt in alternatives:
                        # Compare extracted details with stored alternatives
                        if alt["hospital_name"].lower() == extracted_hospital.lower() and \
                           alt["appointment_date"] == extracted_date and \
                           alt["time_slot"].lower() == extracted_time.replace("IST", "").strip().lower(): # Match time more flexibly
                            selected_alternative = alt
                            break
                except ValueError:
                    # Date parsing failed, means Gemini might have given a bad date or it's not a date
                    print(f"DEBUG: Failed to parse extracted date string: {extracted_date_str}")


        if selected_alternative:
            # --- CRITICAL FIX HERE ---
            # Ensure appointment_date is a datetime.date object before putting into patient_data
            # If it's stored as a string, convert it back.
            if isinstance(selected_alternative["appointment_date"], str):
                try:
                    selected_alternative["appointment_date"] = datetime.strptime(selected_alternative["appointment_date"], '%Y-%m-%d').date()
                except ValueError:
                    print(f"Error converting stored date string: {selected_alternative['appointment_date']}")
                    return "There was an issue processing the selected date. Please try again."
            # --- END CRITICAL FIX ---


            patient_data["selected_hospital"] = selected_alternative["hospital_name"]
            patient_data["selected_time_slot"] = selected_alternative["time_slot"]
            patient_data["selected_appointment_date"] = selected_alternative["appointment_date"]
            patient_data["recommended_package_id"] = selected_alternative.get("package_id", patient_data.get("recommended_package_id"))
            patient_data["recommended_package_name"] = selected_alternative.get("package_name", patient_data.get("recommended_package_name"))

            session["patient_data"] = patient_data
            session["state"] = "confirm_slot"
            session["alternative_slots"] = [] # Clear alternatives after selection

            # This f-string should now work correctly as selected_alternative["appointment_date"] is a date object
            return f"You've selected the slot at {selected_alternative['hospital_name']} on {selected_alternative['appointment_date'].strftime('%Y-%m-%d')} {selected_alternative['time_slot']} IST. Confirm? (Yes/No)"
        else:
            return "I couldn't understand your selection. Please choose an alternative by number (e.g., '1') or by mentioning the hospital and date (e.g., 'Metro Health 2025-08-15'), or say 'no' to look for other options."

        

    elif state == "confirm_slot":
        if "yes" in message.lower():
                package_name = patient_data.get("recommended_package_name")
                hospital_name = patient_data.get("selected_hospital")
                appointment_date = patient_data.get("preferred_date")
                time_slot = patient_data.get("selected_time_slot")

                if not all([package_name, hospital_name, appointment_date, time_slot]):
                    session["state"] = "initial"
                    return "Something went wrong with the appointment details. Please start over."

                ref_number = generate_reference_number()

                # Save appointment (if using Django models)
                patient, created = Patient.objects.get_or_create(
                    name=patient_data['name'],
                    age=patient_data['age'],
                    gender=patient_data['gender'],
                    defaults={'medical_history': patient_data['medical_history']}
                )
                Appointment.objects.create(
                    patient=patient,
                    package_id=patient_data.get("recommended_package_id"),
                    package_name=package_name,
                    hospital_name=hospital_name,
                    appointment_date=appointment_date,
                    appointment_time=time_slot,
                    reference_number=ref_number
                )

                session["state"] = "initial" # Reset state after confirmation
                return f"Checkup confirmed! Reference number: {ref_number}. Anything else?"
        elif "no" in message.lower():
                session["state"] = "recommend_package" # Allow user to choose another date/hospital
                return "No problem. Would you like to check for alternative dates or hospitals, or perhaps a different package?"
        else:
                return "Please confirm with 'Yes' or 'No'." % message

   

    return "I'm not sure how to handle that. Please try rephrasing or ask to 'schedule a checkup' or 'view available packages'."

# def display_available_packages(df):
#     packages = df[['package_name', 'tests_included']].drop_duplicates().to_string(index=False)
#     return f"Here are some of our available packages:\n{packages}"

def display_available_packages(df):
    packages_html = df[['package_name', 'tests_included']].drop_duplicates().to_html(
        index=False, 
        classes='table table-bordered table-hover',  # Add Bootstrap styling
        escape=False, 
        border=0
    )
    return f"<h4>Here are some of our available packages:</h4>{packages_html}"

def recommend_checkup_package(patient_data, df):
    age = patient_data["age"]
    gender = patient_data["gender"].lower() # Ensure consistency in case
    medical_history = patient_data["medical_history"].lower()

    print(f"\n--- Recommendation Debugging ---")
    print(f"Patient Profile: Age={age}, Gender={gender}, Medical History='{medical_history}'")
    print(f"Initial DF shape: {df.shape}")

    # Start with a copy to avoid SettingWithCopyWarning
    filtered_packages = df.copy()

    # Apply Age and Gender Filters
    # Make sure recommended_age is numeric and 0 for 'no min age' as per previous fix
    filtered_packages = filtered_packages[
        (filtered_packages['recommended_age'].apply(lambda x: pd.isna(x) or age >= x)) &
        (filtered_packages['recommended_gender'].apply(lambda x: pd.isna(x) or gender in x.lower()))
    ]
    print(f"After Age/Gender Filter shape: {filtered_packages.shape}")
    print(f"Filtered Packages (Age/Gender):\n{filtered_packages[['package_name', 'recommended_age', 'recommended_gender']].head()}")


    # Tailored recommendations based on medical history
    # Collect potential packages that match ANY relevant criteria, then combine
    medical_history_matches = pd.DataFrame() # Initialize an empty DataFrame

    if "diabetes" in medical_history:
        # Use .copy() to avoid SettingWithCopyWarning if these are filtered again
        diabetes_packages = filtered_packages[
            filtered_packages['medical_history'].str.contains('diabetic screening|blood sugar|diabetes', case=False, na=False) |
            filtered_packages['package_name'].str.contains('diabetes', case=False, na=False)
        ].copy()
        if not diabetes_packages.empty:
            print(f"Diabetes matches found: {diabetes_packages['package_name'].tolist()}")
            medical_history_matches = pd.concat([medical_history_matches, diabetes_packages]).drop_duplicates()

    if "hypertension" in medical_history:
        hypertension_packages = filtered_packages[
            filtered_packages['medical_history'].str.contains('blood pressure|hypertension|cardiac|heart', case=False, na=False) |
            filtered_packages['package_name'].str.contains('cardiac|heart|hypertension', case=False, na=False)
        ].copy()
        if not hypertension_packages.empty:
            print(f"Hypertension matches found: {hypertension_packages['package_name'].tolist()}")
            medical_history_matches = pd.concat([medical_history_matches, hypertension_packages]).drop_duplicates()

    # Age and Gender Specific Tests (apply only if not already filtered by specific medical history)
    gender_age_specific_matches = pd.DataFrame()
    gender = gender.lower().strip()
    age = int(age)

    if gender == "female" and age >= 40:
        women_packages = filtered_packages[
            filtered_packages['tests_included'].str.contains('mammogram|pap smear|gynecology', case=False, na=False) |
            filtered_packages['package_name'].str.contains('women', case=False, na=False)
        ].copy()
        if not women_packages.empty:
            print(f"Women's health matches found (age >= 40): {women_packages['package_name'].tolist()}")
            gender_age_specific_matches = pd.concat([gender_age_specific_matches, women_packages]).drop_duplicates()
            
    # if gender == "male" and 40 >= age <= 60:
    #     mens_health_packages = filtered_packages[
    #         filtered_packages['tests_included'].str.contains("prostate screening|ECG", case=False, na=False) |
    #         filtered_packages['package_name'].str.contains("men's health plus", case=False, na=False)             
    #     ].copy()
    #     if not mens_health_packages.empty:
    #         print(f"Men's health matches found (age 40–60): {mens_health_packages['package_name'].tolist()}")
    #         gender_age_specific_matches = pd.concat([gender_age_specific_matches, mens_health_packages]).drop_duplicates()

    if age >= 50:
        colonoscopy_packages = filtered_packages[
            filtered_packages['tests_included'].str.contains('colonoscopy|colorectal', case=False, na=False) |
            filtered_packages['package_name'].str.contains('colon', case=False, na=False)
        ].copy()
        if not colonoscopy_packages.empty:
            print(f"Colonoscopy matches found (age >= 50): {colonoscopy_packages['package_name'].tolist()}")
            gender_age_specific_matches = pd.concat([gender_age_specific_matches, colonoscopy_packages]).drop_duplicates()

    # Combine all relevant recommendations
    # Prioritize specific medical history, then age/gender specific, then general
    final_recommendations = pd.DataFrame()
    if not medical_history_matches.empty:
        final_recommendations = pd.concat([final_recommendations, medical_history_matches]).drop_duplicates()
    if not gender_age_specific_matches.empty:
        # Only add if not already covered by specific medical history
        final_recommendations = pd.concat([final_recommendations, gender_age_specific_matches]).drop_duplicates()

    # Fallback: If no specific package is found, suggest a general one that matches basic age/gender criteria
    if final_recommendations.empty:
        # Find any package matching basic age/gender, or just any package
        general_packages = filtered_packages.copy()
        if not general_packages.empty:
            # Sort by comprehensive-ness or some other default
            final_recommendations = general_packages.sort_values(by='package_name').iloc[[0]] # Get one general package
        else:
            final_recommendations = df.iloc[[0]] # If absolutely nothing, just pick the first one

    print(f"Final recommendations shape: {final_recommendations.shape}")
    print(f"Final recommended packages:\n{final_recommendations[['package_name', 'tests_included']]}")

    if not final_recommendations.empty:
        # Prioritize (e.g., by more specific matches first, then broader)
        # For simplicity here, just pick the first one from the combined list.
        recommended_package = final_recommendations.iloc[0]
        patient_data["recommended_package_name"] = recommended_package["package_name"]
        patient_data["recommended_package_id"] = recommended_package["package_id"]
        return f"Based on your profile, I recommend the \"{recommended_package['package_name']}\" package (includes {recommended_package['tests_included']})."
    else:
        return "I couldn't find a specific package for your profile. We offer general health checkups."

def display_available_slots(patient_data, df, current_session_data):
    preferred_date = patient_data.get("preferred_date")
    recommended_package_id = patient_data.get("recommended_package_id")

    if not preferred_date or not recommended_package_id:
        return "I need more information to check slots. Please tell me your preferred date and I'll recommend a package."

    # Filter by package and preferred date
    # Make sure preferred_date is a datetime object for comparison with df['date']
    preferred_date_dt = pd.to_datetime(preferred_date)

    available_slots_on_date = df[
        (df['package_id'] == recommended_package_id) &
        (df['date'] == preferred_date_dt) # Use the converted datetime object
    ]

    # --- SYNTAX FIX: Use 'available_slots_on_date' consistently ---
    if not available_slots_on_date.empty:
        selected_slot = available_slots_on_date.iloc[0]
        patient_data["selected_hospital"] = selected_slot["hospital_name"]
        patient_data["selected_time_slot"] = selected_slot["time_slot"]
        # CRITICAL: Store as a date object if it came as a Timestamp from pd.to_datetime
        patient_data["selected_appointment_date"] = selected_slot["date"].date()

        current_session_data["patient_data"] = patient_data
        current_session_data["state"] = "confirm_slot"
        return f"Checking availability... Available slot at {selected_slot['hospital_name']} on {selected_slot['date'].strftime('%Y-%m-%d')} {selected_slot['time_slot']} IST. Confirm? (Yes/No)"
    else:
        # Limited Availability: Suggest alternatives
        alternative_slots_df = df[ # <--- Use alternative_slots_df (consistent with previous full solution)
            (df['package_id'] == recommended_package_id) &
            (df['date'] > preferred_date_dt) # Look for future dates
        ].sort_values(by='date').head(5) # Get up to 5 alternatives (consistent with previous full solution)

        if not alternative_slots_df.empty:
            # alt_info = []
            # current_session_data["alternative_slots"] = []

            # for i, row in alternative_slots_df.iterrows():
            #     alt_str = f"{i+1}. {row['hospital_name']} on {row['date'].strftime('%Y-%m-%d')} {row['time_slot']} IST"
            #     alt_info.append(alt_str)
            #     current_session_data["alternative_slots"].append({
            #         "hospital_name": row['hospital_name'],
            #         # CRITICAL: Always store as a pure date object
            #         "appointment_date": row['date'].date(),
            #         "time_slot": row['time_slot'],
            #         "package_id": row['package_id'],
            #         "package_name": patient_data.get("recommended_package_name")
            #     })
            from django.utils.html import escape  # Optional for extra safety

            # Prepare HTML table for alternatives
            alt_table_rows = []
            current_session_data["alternative_slots"] = []

            for i, (_, row) in enumerate(alternative_slots_df.iterrows(), start=1):
                alt_table_rows.append(f"""
                    <tr>
                        <td>{i}</td>
                        <td>{escape(row['hospital_name'])}</td>
                        <td>{row['date'].strftime('%Y-%m-%d')}</td>
                        <td>{row['time_slot']} IST</td>
                    </tr>
                """)
                current_session_data["alternative_slots"].append({
                    "hospital_name": row['hospital_name'],
                    "appointment_date": row['date'].date(),
                    "time_slot": row['time_slot'],
                    "package_id": row['package_id'],
                    "package_name": patient_data.get("recommended_package_name")
                })

            alt_table_html = f"""
                <h4>No slots available on {preferred_date.strftime('%Y-%m-%d')}. Here are some alternatives:</h4>
                <table class='table table-bordered table-hover'>
                    <thead>
                        <tr>
                            <th>#</th>
                            <th>Hospital</th>
                            <th>Date</th>
                            <th>Time Slot</th>
                        </tr>
                    </thead>
                    <tbody>
                        {''.join(alt_table_rows)}
                    </tbody>
                </table>
                <p>Please select an option by number (e.g., '1') or by mentioning the hospital/date.</p>
            """

            current_session_data["state"] = "select_alternative_slot"
            return alt_table_html
            
            # current_session_data["state"] = "select_alternative_slot"
            # return f"No slots available on {preferred_date.strftime('%Y-%m-%d')}. However, here are some alternatives:\n" + "\n".join(alt_info) + "\nPlease select an option by number (e.g., '1') or by mentioning the hospital/date."
        
        else:
            current_session_data["state"] = "initial"
            return "Sorry, no immediate slots or alternatives are available for that package. Please try a different package or contact the hospital directly."

def generate_reference_number():
    return "CHK" + str(uuid.uuid4()).replace("-", "")[:9].upper() # Example simple reference

def chat_interface(request):
    return render(request, 'chatbot/chat.html')


import pandas as pd
import os
from django.conf import settings
import numpy as np

def load_checkups_data():
    csv_path = os.path.join(settings.BASE_DIR, 'chatbot', 'checkups_data.csv') # Adjust path as needed
    try:
        df = pd.read_csv(csv_path)
        df['date'] = pd.to_datetime(df['date'])
        # Convert to numeric, coercing errors to NaN
        df['recommended_age'] = pd.to_numeric(df['recommended_age'], errors='coerce')
        # Fill NaN values with a suitable default, e.g., 0, or a very low number if it means "no minimum age"
        # Using 0 means it will match any age >= 0
        df['recommended_age'] = df['recommended_age'].fillna(0).astype(int)
 
 
        return df
    except FileNotFoundError:
        print(f"Error: checkups_data.csv not found at {csv_path}")
        return pd.DataFrame()
 
 
checkups_df = load_checkups_data()
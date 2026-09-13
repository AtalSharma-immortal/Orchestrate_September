import pandas as pd
from image_parser import ImageParser

def main():
    print("Loading datasets...")
    events_df = pd.read_csv("dataset/financial_events.csv")
    images_df = pd.read_csv("dataset/images.csv")

    missing_events = events_df[events_df["amount"].isna()]
    print(f"Found {len(missing_events)} events with missing amounts.\n")

    parser = ImageParser(media_dir="dataset/media/images")

    for idx, row in missing_events.iterrows():
        event_id = row["event_id"]
        amount = parser.get_amount_for_event(event_id, images_df)
        print(f"Event ID: {event_id}  -->  Extracted Amount: {amount}")

if __name__ == "__main__":
    main()# code/test_parser.py
import os
import sys
import pandas as pd

# Add current folder to Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from image_parser import ImageParser

def main():
    print("Loading datasets...")
    events_df = pd.read_csv("dataset/financial_events.csv")
    images_df = pd.read_csv("dataset/images.csv")

    missing_events = events_df[events_df["amount"].isna()]
    print(f"Found {len(missing_events)} events with missing amounts.\n")

    parser = ImageParser(media_dir="dataset/media/images")

    for idx, row in missing_events.iterrows():
        event_id = row["event_id"]
        amount = parser.get_amount_for_event(event_id, images_df)
        print(f"Event ID: {event_id}  -->  Extracted Amount: {amount}")

if __name__ == "__main__":
    main()
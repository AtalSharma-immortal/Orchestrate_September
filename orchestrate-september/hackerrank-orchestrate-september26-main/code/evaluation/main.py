import os
import pandas as pd

def evaluate_pipeline():
    print("--- Running Evaluation Metrics ---")
    
    events_path = "dataset/financial_events.csv"
    output_path = "dataset/output.csv"
    
    if not os.path.exists(output_path):
        print(f"Error: Output file not found at {output_path}")
        return

    original_df = pd.read_csv(events_path)
    processed_df = pd.read_csv(output_path)
    
    initial_nulls = original_df["amount"].isna().sum()
    final_nulls = processed_df["amount"].isna().sum()
    total_records = len(processed_df)
    
    print(f"Total Financial Events Processed : {total_records}")
    print(f"Initial Missing Amounts          : {initial_nulls}")
    print(f"Final Missing Amounts            : {final_nulls}")
    print(f"Extraction Completion Rate       : {((initial_nulls - final_nulls) / initial_nulls) * 100:.2f}%")
    print("--- Evaluation Completed Successfully ---")

if __name__ == "__main__":
    evaluate_pipeline()
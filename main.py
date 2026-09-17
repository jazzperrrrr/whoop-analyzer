import csv
from datetime import date
from pathlib import Path


# Record today's date automatically and ask for today's WHOOP measurements.
today = date.today().isoformat()
recovery = float(input("Recovery percentage: "))
hrv = float(input("HRV in ms: "))
resting_heart_rate = float(input("Resting heart rate in bpm: "))
sleep_performance = float(input("Sleep performance percentage: "))
day_strain = float(input("Day strain: "))

# Keep the CSV in the same folder as this program.
csv_file = Path(__file__).resolve().parent / "whoop_data.csv"
needs_header = not csv_file.exists() or csv_file.stat().st_size == 0

# Append mode ("a") adds a row without deleting previous records.
# newline="" prevents extra blank lines when writing CSV files on Windows.
with csv_file.open("a", newline="", encoding="utf-8") as file:
    writer = csv.writer(file)

    # Write column headers only when the file is new or empty.
    if needs_header:
        writer.writerow([
            "date",
            "recovery_percentage",
            "hrv_ms",
            "resting_heart_rate_bpm",
            "sleep_performance_percentage",
            "day_strain",
        ])

    writer.writerow([
        today,
        recovery,
        hrv,
        resting_heart_rate,
        sleep_performance,
        day_strain,
    ])

print("Today's data has been saved to whoop_data.csv.")

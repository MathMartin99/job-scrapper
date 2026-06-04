import json
import sys

JOBS_PATH = "app/data/jobs/jobs_raw.json"

def load_jobs(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_jobs(path, jobs):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(jobs, f, ensure_ascii=False, indent=2)

def main():
    if len(sys.argv) < 2:
        print("Usage: python app\\src\\jobs\\update_job_description.py <job_id>")
        return

    job_id = sys.argv[1]
    jobs = load_jobs(JOBS_PATH)

    for job in jobs:
        if job["id"] == job_id:
            print(f"Offre trouvée: {job['title']} - {job['company']} - {job['location']}")
            print("Colle la nouvelle description, puis termine par une ligne contenant uniquement: END")
            lines = []
            while True:
                line = input()
                if line.strip() == "END":
                    break
                lines.append(line)
            job["description"] = "\n".join(lines).strip()
            save_jobs(JOBS_PATH, jobs)
            print("Description mise à jour.")
            return

    print("Job ID introuvable.")

if __name__ == "__main__":
    main()
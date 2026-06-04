from seleniumbase import SB
import json
import time
import random

JOBS_PATH = "app/data/jobs/jobs_raw.json"

def load_jobs():
    with open(JOBS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def save_jobs(jobs):
    with open(JOBS_PATH, "w", encoding="utf-8") as f:
        json.dump(jobs, f, ensure_ascii=False, indent=2)

def human_delay():
    time.sleep(random.uniform(2, 4))

def scrape_job(sb, job):
    print(f"Scraping: {job['url']}")

    sb.open(job["url"])
    human_delay()

    # scroll pour simuler utilisateur
    sb.scroll_to_bottom()
    human_delay()

    title = ""
    company = ""
    location = ""
    description = ""

    try:
        title = sb.get_text("h1")
    except:
        pass

    selectors_company = [
        "[data-testid='inlineHeader-companyName']",
        ".jobsearch-InlineCompanyRating div",
    ]

    for sel in selectors_company:
        try:
            company = sb.get_text(sel)
            if company:
                break
        except:
            pass

    selectors_location = [
        "[data-testid='job-location']",
        "[data-testid='inlineHeader-companyLocation']",
    ]

    for sel in selectors_location:
        try:
            location = sb.get_text(sel)
            if location:
                break
        except:
            pass

    selectors_description = [
        "#jobDescriptionText",
        "[data-testid='jobsearch-JobComponent-description']",
    ]

    for sel in selectors_description:
        try:
            description = sb.get_text(sel)
            if description:
                break
        except:
            pass

    print(f"  title: {title[:60]}")
    print(f"  company: {company[:60]}")
    print(f"  location: {location[:60]}")
    print(f"  description_length: {len(description)}")

    if title:
        job["title"] = title
    if company:
        job["company"] = company
    if location:
        job["location"] = location
    if description:
        job["description"] = description

def main():
    jobs = load_jobs()

    to_scrape = [j for j in jobs if not j.get("description")]
    already_done = len(jobs) - len(to_scrape)
    if already_done:
        print(f"{already_done} offres déjà scrappées, ignorées.")

    with SB(uc=True, headless=False) as sb:
        for job in to_scrape:
            scrape_job(sb, job)

    save_jobs(jobs)
    print("Scraping terminé.")

if __name__ == "__main__":
    main()
from scripts.parse_upwork import job_id_from_link, parse_description, persona_domain

FIXED = (
    "We need ads.- Setting up pixels.Budget: $500Posted On: February 17, 2024 09:09 UTCCategory: Social Media "
    "MarketingSkills:Facebook Advertising,     Social Media Advertising,     Google AdsSkills:        "
    "Facebook Advertising,                     Social Media Advertising,                     Google Adsclick to apply"
)
WITH_COUNTRY = (
    "Job Title: FS dev. Budget: see below. Requirements: x.Budget: $1,100Posted On: February 17, 2024 09:09 "
    "UTCCategory: Full Stack DevelopmentSkills:AngularJS,     MongoDB,     APISkills:        AngularJS,       "
    "              MongoDB,                     APICountry: United Statesclick to apply"
)
HOURLY = "Do thing.Hourly Range: $15.00-$30.00Posted On: Feb 1, 2024 UTCCategory: Data AnalyticsSkills:SQL,  Tableauclick to apply"


def test_fixed_budget_and_dedup_skills():
    r = parse_description(FIXED)
    assert r["category"] == "Social Media Marketing"
    assert r["skills"] == ["Facebook Advertising", "Social Media Advertising", "Google Ads"]
    assert r["budget_text"] == "Budget: $500"
    assert r["body"] == "We need ads.- Setting up pixels."


def test_body_containing_budget_word_and_country():
    r = parse_description(WITH_COUNTRY)
    assert r["body"].endswith("x.")
    assert r["country_text"] == "United States"
    assert r["skills"] == ["AngularJS", "MongoDB", "API"]


def test_hourly():
    r = parse_description(HOURLY)
    assert r["budget_text"].startswith("Hourly Range")
    assert r["skills"] == ["SQL", "Tableau"]


def test_persona_domain_and_id():
    assert persona_domain("Data Analytics", ["SQL"]) == "data_analyst"
    assert persona_domain("Full Stack Development", ["React"]) == "fullstack_web"
    assert persona_domain("Graphic Design", ["Logo Design"]) == "other"
    assert persona_domain("Thai Translation", []) == "other"  # "ai" must not match inside words
    assert job_id_from_link("https://x/jobs/A_%7E011604a02b364315a8?source=rss") == "011604a02b364315a8"

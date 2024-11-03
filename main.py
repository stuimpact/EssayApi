from fastapi import FastAPI, HTTPException, Request, Depends, Security
from pydantic import BaseModel
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup
import logging
import re
import time
from pymongo import MongoClient
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from fastapi.security.api_key import APIKeyHeader

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = FastAPI()

client = MongoClient('mongodb+srv://mrithunjay26:77820897@comments.jx9xmpc.mongodb.net/')
db = client['college_db']
collection = db['essay_prompts']
api_key_collection = db['api_keys']  # Collection for storing API keys
limiter = Limiter(key_func=get_remote_address)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = FastAPI()
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, lambda request, exc: HTTPException(status_code=429, detail="Rate limit exceeded"))
app.add_middleware(SlowAPIMiddleware)
API_KEY_NAME = "x-api-key"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=True)

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, lambda request, exc: HTTPException(status_code=429, detail="Rate limit exceeded"))
app.add_middleware(SlowAPIMiddleware)
class CollegeRequest(BaseModel):
    college_name: str

class MultipleCollegesRequest(BaseModel):
    college_names: str
async def verify_api_key(api_key: str = Security(api_key_header)):
    if not api_key_collection.find_one({"key": api_key}):
        raise HTTPException(status_code=403, detail="Invalid API Key")
    return api_key

@app.middleware("http")
async def log_request_time(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    process_time = time.time() - start_time
    print(f"Request to {request.url.path} took {process_time:.4f} seconds")
    return response

def setup_driver():
    chrome_options = Options()
    chrome_options.add_argument('--headless')
    chrome_options.add_argument('--disable-gpu')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument('--window-size=1920x1080')
    service = Service()
    driver = webdriver.Chrome(service=service, options=chrome_options)
    return driver

def clean_text(text):
    """Remove unnecessary spaces, newlines, and tabs."""
    return re.sub(r'\s+', ' ', text.strip())

def fetch_essay_prompts_from_selenium(driver, college_name):
    start_time = time.time()
    url = 'https://www.collegevine.com/college-essay-prompts#search-results'
    driver.get(url)

    try:
        search_box = WebDriverWait(driver, 5).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "input[aria-label='Search for a school...']"))
        )
        search_box.clear()
        search_box.send_keys(college_name)

        search_button = driver.find_element(By.CSS_SELECTOR, "a.btn.btn-sm.btn-primary")
        search_button.click()

        view_prompts_button = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.XPATH, "//a[contains(text(), 'View Essay Prompts')]"))
        )
        view_prompts_button.click()

        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div.card"))
        )

        page_source = driver.page_source
        soup = BeautifulSoup(page_source, 'html.parser')
        prompts = []

        college_header = soup.find('h1', class_='header-title')
        scraped_college_name = clean_text(college_header.text) if college_header else college_name

        cards = soup.find_all('div', class_='card')
        for card in cards:
            title = clean_text(card.find('h3', class_='mt-2').text) if card.find('h3', class_='mt-2') else "No title"
            required = clean_text(card.find('div', class_='badge').text) if card.find('div', class_='badge') else "No requirement"
            word_count = clean_text(card.find('span', class_='text-secondary').text) if card.find('span', class_='text-secondary') else "No word count"
            description = clean_text(card.find_all('p')[1].text) if len(card.find_all('p')) > 1 else "No description"

            options = []
            option_elements = card.find_all('div', class_='row')
            for option_element in option_elements:
                option_title = clean_text(option_element.find('h5').text) if option_element.find('h5') else None
                option_description = clean_text(option_element.find_all('p')[1].text) if len(option_element.find_all('p')) > 1 else None
                if option_title and option_description:
                    options.append({
                        "option_title": option_title,
                        "option_description": option_description
                    })

            prompt = {
                'title': title,
                'required': required,
                'word_count': word_count,
                'description': description,
                'options': options if options else None
            }

            if title != "No title" and description != "No description":
                prompts.append(prompt)

        end_time = time.time()
        print(f"fetch_essay_prompts_from_selenium took {end_time - start_time:.4f} seconds")
        return scraped_college_name, prompts
    except Exception as e:
        logging.error(f'Error fetching prompts for {college_name}: {e}')
        return college_name, []

def get_prompts_for_college(college_name):
    """Helper function to get prompts for a single college."""
    cached_data = collection.find_one({"college_name": college_name})
    if cached_data:
        logging.info(f"Found cached essay prompts for {college_name}")
        return {"college_name": college_name, "prompts": cached_data['prompts']}

    driver = setup_driver()
    try:
        logging.info(f"Fetching essay prompts for {college_name} from Selenium")
        scraped_college_name, prompts = fetch_essay_prompts_from_selenium(driver, college_name)
        if not prompts:
            return None

        existing_data = collection.find_one({"college_name": scraped_college_name})
        if existing_data:
            logging.info(f"Found cached essay prompts for {scraped_college_name}")
            return {"college_name": scraped_college_name, "prompts": existing_data['prompts']}

        collection.insert_one({"college_name": scraped_college_name, "prompts": prompts})
        return {"college_name": scraped_college_name, "prompts": prompts}
    except Exception as e:
        logging.error(f"An error occurred: {e}")
        return None
    finally:
        driver.quit()

@app.post("/get_essay_prompts/", dependencies=[Depends(verify_api_key), Depends(limiter.limit("5/minute"))])
def get_essay_prompts(request: CollegeRequest):
    result = get_prompts_for_college(request.college_name)
    if result:
        return result
    else:
        raise HTTPException(status_code=404, detail=f"No essay prompts found for {request.college_name}.")

@app.post("/get_multiple_essay_prompts/", dependencies=[Depends(verify_api_key), Depends(limiter.limit("5/minute"))])
def get_multiple_essay_prompts(request: MultipleCollegesRequest):
    college_names = [name.strip() for name in request.college_names.split(',')]
    results = {}

    for college_name in college_names:
        result = get_prompts_for_college(college_name)
        if result:
            results[college_name] = result
        else:
            results[college_name] = {"error": f"No essay prompts found for {college_name}."}

    return results

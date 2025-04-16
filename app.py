import os
import json
import re
import time
import pickle
import requests
from time import sleep
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException, WebDriverException, TimeoutException
from flask import Flask, request, jsonify, send_from_directory
from threading import Thread
from flask_cors import CORS
import platform

app = Flask(__name__, static_folder="static")
CORS(app)  # Enable CORS for all routes

# Global variables to store state
driver = None
is_logged_in = False
is_scraping = False
current_job = None
job_results = {}

# Configuration for LinkedIn credentials and cookies
LINKEDIN_USERNAME = "prince0862gupta@gmail.com"  # Replace with your LinkedIn email
LINKEDIN_PASSWORD = "Prince297#"  # Replace with your LinkedIn password
COOKIES_FILE = "linkedin_cookies.pkl"

def setup_driver(headless=False):
    """Set up and return a WebDriver instance for any available browser"""
    from selenium import webdriver
    from selenium.common.exceptions import WebDriverException
    import platform
    
    system = platform.system()  # Windows, Darwin (macOS), or Linux
    print(f"Detected operating system: {system}")

    # Set common arguments
    browser_options = {
        "window_size": "--window-size=1366,768",
        "no_sandbox": "--no-sandbox",
        "disable_dev_shm": "--disable-dev-shm-usage",
        "headless": "--headless" if headless else None,
        "disable_gpu": "--disable-gpu" if headless else None,
        "disable_blink_features": "--disable-blink-features=AutomationControlled",  # Hide automation
        "user_agent": "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/96.0.4664.110 Safari/537.36"
    }
    
    # List of browsers to try, in order of preference
    browsers = ["chrome", "firefox", "edge", "safari"]
    
    # If on Windows, prioritize Edge
    if system == "Windows":
        browsers = ["edge", "chrome", "firefox"]
    # If on macOS, include Safari
    elif system == "Darwin":
        browsers = ["chrome", "safari", "firefox", "edge"]
    
    for browser_name in browsers:
        try:
            print(f"Attempting to use {browser_name.capitalize()} browser...")
            
            if browser_name == "chrome":
                options = webdriver.ChromeOptions()
                for arg in browser_options.values():
                    if arg:
                        options.add_argument(arg)
                # Add experimental options to avoid detection
                options.add_experimental_option("excludeSwitches", ["enable-automation"])
                options.add_experimental_option('useAutomationExtension', False)
                driver = webdriver.Chrome(options=options)
                # Execute CDP commands to avoid detection
                driver.execute_cdp_cmd('Network.setUserAgentOverride', {"userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/96.0.4664.110 Safari/537.36"})
                driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            
            elif browser_name == "firefox":
                options = webdriver.FirefoxOptions()
                if browser_options["headless"]:
                    options.add_argument("-headless")
                # Set Firefox profile settings to avoid detection
                options.set_preference("dom.webdriver.enabled", False)
                options.set_preference("useAutomationExtension", False)
                driver = webdriver.Firefox(options=options)
            
            elif browser_name == "edge":
                options = webdriver.EdgeOptions()
                for arg in browser_options.values():
                    if arg:
                        options.add_argument(arg)
                # Add experimental options to avoid detection
                options.add_experimental_option("excludeSwitches", ["enable-automation"])
                options.add_experimental_option('useAutomationExtension', False)
                driver = webdriver.Edge(options=options)
            
            elif browser_name == "safari" and system == "Darwin":
                # Safari doesn't support headless mode
                driver = webdriver.Safari()
            
            else:
                continue  # Skip unsupported browsers
            
            print(f"Successfully initialized {browser_name.capitalize()} browser")
            return driver
        
        except (WebDriverException, Exception) as e:
            print(f"{browser_name.capitalize()} browser not available: {e}")
    
    # If we get here, no browser was available
    raise Exception("No compatible browser found. Please ensure Chrome, Firefox, Edge, or Safari is installed with the corresponding WebDriver.")

def save_cookies(driver, filename=COOKIES_FILE):
    """Save browser cookies to a file"""
    if driver:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(filename)), exist_ok=True)
            pickle.dump(driver.get_cookies(), open(filename, "wb"))
            print(f"Cookies saved to {filename}")
            return True
        except Exception as e:
            print(f"Error saving cookies: {e}")
    return False

def load_cookies(driver, filename=COOKIES_FILE):
    """Load cookies from file and add them to the browser session"""
    if not os.path.exists(filename):
        print(f"Cookie file {filename} not found")
        return False
    
    try:
        cookies = pickle.load(open(filename, "rb"))
        
        # Make sure we're on linkedin.com domain before adding cookies
        current_url = driver.current_url
        if "linkedin.com" not in current_url:
            driver.get("https://www.linkedin.com")
            sleep(2)
            
        # Add cookies to browser
        for cookie in cookies:
            try:
                # Skip cookies that might cause issues
                if 'expiry' in cookie:
                    # Convert from seconds to milliseconds
                    expiry = cookie['expiry']
                    if isinstance(expiry, int):
                        cookie['expiry'] = expiry
                    
                driver.add_cookie(cookie)
            except Exception as e:
                print(f"Error adding cookie {cookie.get('name')}: {e}")
                
        print("Cookies loaded successfully")
        return True
    except Exception as e:
        print(f"Error loading cookies: {e}")
        return False

def check_for_security_challenge(driver):
    """Check if there's a security challenge or verification page"""
    try:
        # Check for terms like "verification" or "challenge" in the URL or page elements
        current_url = driver.current_url
        if any(term in current_url for term in ["checkpoint", "challenge", "verify"]):
            return True
            
        # Check for verification-related elements
        verification_elements = driver.find_elements(By.CSS_SELECTOR, 
            "input[id*='verification'], input[id*='pin'], input[id*='code'], div[contains(text(), 'Two-step')]")
            
        return len(verification_elements) > 0
    except:
        return False

def check_login_status(driver):
    """Check if we're logged in to LinkedIn"""
    try:
        # Try to find elements that would indicate we're logged in
        feed_elements = driver.find_elements(By.ID, "global-nav")
        login_elements = driver.find_elements(By.ID, "username")
        
        # If we're on the feed or don't see login form, we're probably logged in
        return len(feed_elements) > 0 or len(login_elements) == 0
    except Exception as e:
        print(f"Error checking login status: {e}")
        return False

def auto_login(username=LINKEDIN_USERNAME, password=LINKEDIN_PASSWORD, use_cookies=True):
    """Automated login to LinkedIn using credentials"""
    global driver, is_logged_in
    
    print("\n" + "="*80)
    print("STARTING LINKEDIN AUTO LOGIN PROCESS")
    print("="*80)
    
    # Create a visible browser window
    driver = setup_driver(headless=False)  # Start visible for login, will go headless later
    driver.get('https://www.linkedin.com/login')
    print(f"Current page title: {driver.title}")
    
    # Try to load cookies first if enabled
    if use_cookies and load_cookies(driver):
        # Refresh the page to apply cookies
        driver.get('https://www.linkedin.com/feed/')
        sleep(3)
        
        # Check if we're logged in
        if check_login_status(driver):
            print("✅ Successfully logged in using saved cookies!")
            is_logged_in = True
            
            # Switch to headless mode
            if switch_to_headless():
                print("Browser window hidden successfully")
            
            print("="*80)
            print("LOGIN PROCESS COMPLETED SUCCESSFULLY")
            print("="*80 + "\n")
            return True
        else:
            print("Cookies didn't work for login, trying with credentials...")
    
    # If we get here, cookies didn't work or weren't used, proceed with credentials
    try:
        print("Attempting login with credentials...")
        
        # Clear any existing inputs
        driver.find_element(By.ID, "username").clear()
        driver.find_element(By.ID, "password").clear()
        
        # Enter credentials
        driver.find_element(By.ID, "username").send_keys(username)
        driver.find_element(By.ID, "password").send_keys(password)
        
        # Click the sign-in button
        driver.find_element(By.CSS_SELECTOR, "button[type='submit']").click()
        print("Login credentials submitted")
        
        # Wait for possible security challenges
        waiting_start_time = time.time()
        max_wait_time = 60  # 1 minute max wait
        
        while time.time() - waiting_start_time < max_wait_time:
            # If we're on the feed, we're logged in
            if "feed" in driver.current_url:
                break
            
            # Check for security challenges
            if check_for_security_challenge(driver):
                print("⚠️ Security verification detected!")
                print("LinkedIn is requiring verification. Manual intervention needed.")
                print("Please complete the verification in the browser window within 60 seconds...")
                # Give user time to complete verification
                time_spent = int(time.time() - waiting_start_time)
                time_remaining = max(0, max_wait_time - time_spent)
                sleep(time_remaining)
                break
            
            # Check if we're logged in after each iteration
            if check_login_status(driver):
                break
                
            # Small wait before checking again
            sleep(2)
        
        # Final check if we're logged in
        if check_login_status(driver):
            print("✅ Login successful!")
            is_logged_in = True
            
            # Save cookies for future use
            save_cookies(driver)
            
            # Switch to headless mode
            if switch_to_headless():
                print("Browser window hidden successfully")
            
            print("="*80)
            print("LOGIN PROCESS COMPLETED SUCCESSFULLY")
            print("="*80 + "\n")
            return True
        else:
            print("❌ Login failed after attempts")
            is_logged_in = False
            if driver:
                driver.quit()
                driver = None
            return False
            
    except Exception as e:
        print(f"Error during login process: {e}")
        is_logged_in = False
        if driver:
            driver.quit()
            driver = None
        return False

def switch_to_headless():
    """Switch the current visible Chrome driver to headless mode"""
    global driver, is_logged_in
    
    if not driver or not is_logged_in:
        print("No active driver to switch to headless mode")
        return False
    
    try:
        # Get the current cookies from the visible browser
        cookies = driver.get_cookies()
        current_url = driver.current_url
        
        print("Switching to headless mode...")
        
        # Save the current window handle to restore later if needed
        original_window = driver.current_window_handle
        
        # Create a new headless driver
        headless_driver = setup_driver(headless=True)
        
        # Navigate to LinkedIn
        headless_driver.get("https://www.linkedin.com")
        
        # Add all cookies to maintain the session
        for cookie in cookies:
            # Some cookies might cause issues, so we'll try to add each one individually
            try:
                headless_driver.add_cookie(cookie)
            except Exception as e:
                print(f"Warning: Could not add cookie {cookie.get('name')}: {e}")
        
        # Navigate to the same page the user was on
        headless_driver.get(current_url)
        sleep(2)
        
        # Verify we're still logged in
        if "feed" in headless_driver.current_url or not headless_driver.find_elements(By.ID, 'username'):
            print("Successfully switched to headless mode while maintaining login session")
            
            # Close the original visible browser
            driver.quit()
            
            # Update the global driver instance
            driver = headless_driver
            return True
        else:
            print("Failed to maintain login session in headless mode")
            headless_driver.quit()
            return False
    except Exception as e:
        print(f"Error switching to headless mode: {e}")
        return False

def ensure_login():
    """Check if we're logged in, and if not, initiate auto login"""
    global driver, is_logged_in
    
    # Always check driver validity even if logged in
    if is_logged_in and driver:
        try:
            # Test if driver is still responsive
            current_url = driver.current_url
            print(f"Driver check: Current URL is {current_url}")
            
            # Check if we're still logged in
            if check_login_status(driver):
                return True
            else:
                print("Session expired, need to log in again")
                is_logged_in = False
        except Exception as e:
            print(f"Driver is no longer valid: {e}")
            is_logged_in = False
            driver = None
    
    # If we get here, either is_logged_in was False or driver was invalid
    try:
        # We need a new login session
        return auto_login()
    except Exception as e:
        print(f"Login failed: {str(e)}")
        return False

# The rest of the functions remain the same as in the original code
# Only change the calls from manual_login() to auto_login()

def scrape_company_about_page(driver, company_url):
    """Scrape the "About" page of a LinkedIn company"""
    about_url = f"{company_url}/about/"
    print(f"Navigating to the about page: {about_url}")
    driver.get(about_url)
    sleep(3)
    
    if "login" in driver.current_url:
        print("Session expired or not logged in. Cannot scrape about page.")
        return {}
    
    about_data = {
        'website': "",
        'phone': "",
        'associated_members': "",
        'founded': "",
        'specialties': "",
        'description': "",
        'headquarter': "",
        'industry': "",
        'company_size': ""
    }
    
    page_source = driver.page_source
    soup = BeautifulSoup(page_source, 'lxml')
    
    try:
        description_p = soup.find('p', {'class': 'break-words white-space-pre-wrap t-black--light text-body-medium'})
        if description_p:
            about_data['description'] = description_p.get_text().strip()
            print(f"Found company description: {about_data['description'][:50]}...")
    except Exception as e:
        print(f"Error extracting company description: {e}")
    
    try:
        dt_elements = soup.find_all('dt')
        
        for dt in dt_elements:
            header_h3 = dt.find('h3', {'class': 'text-heading-medium'})
            if not header_h3:
                continue
                
            header_text = header_h3.get_text().strip()
            
            dd = dt.find_next('dd')
            if not dd:
                continue
                
            if "Website" in header_text:
                website_link = dd.find('a')
                if website_link:
                    about_data['website'] = website_link.get_text().strip()
                    print(f"Found website: {about_data['website']}")
                    
            elif "Phone" in header_text:
                phone_link = dd.find('a')
                if phone_link:
                    phone_span = phone_link.find('span', {'class': 'link-without-visited-state'})
                    if phone_span:
                        about_data['phone'] = phone_span.get_text().strip()
                        print(f"Found phone: {about_data['phone']}")
                        
            elif "Industry" in header_text:
                about_data['industry'] = dd.get_text().strip()
                print(f"Found industry: {about_data['industry']}")
                
            elif "Company size" in header_text:
                size_text = dd.get_text().strip()
                about_data['company_size'] = size_text
                print(f"Found company size: {about_data['company_size']}")
                
                associated_dd = dd.find_next('dd')
                if associated_dd:
                    associated_link = associated_dd.find('a')
                    if associated_link:
                        about_data['associated_members'] = associated_link.get_text().strip()
                        print(f"Found associated members: {about_data['associated_members']}")
                        
            elif "Headquarters" in header_text:
                about_data['headquarter'] = dd.get_text().strip()
                print(f"Found headquarters: {about_data['headquarter']}")
                
            elif "Founded" in header_text:
                about_data['founded'] = dd.get_text().strip()
                print(f"Found founded year: {about_data['founded']}")
                
            elif "Specialties" in header_text:
                about_data['specialties'] = dd.get_text().strip()
                print(f"Found specialties: {about_data['specialties'][:50]}...")
    
    except Exception as e:
        print(f"Error processing about page structure: {e}")
    
    if not about_data['associated_members']:
        try:
            associated_link = soup.find('a', string=lambda t: t and 'associated members' in t.lower())
            if associated_link:
                about_data['associated_members'] = associated_link.get_text().strip()
                print(f"Found associated members (direct approach): {about_data['associated_members']}")
        except Exception as e:
            print(f"Error finding associated members: {e}")
    
    return about_data

def scrape_company_basics(driver, url):
    """Scrape basic information from a LinkedIn company page"""
    driver.get(url)
    print(f"Scraping company page: {url}")
    sleep(2)
    
    if "login" in driver.current_url:
        print("Session expired or not logged in. Cannot scrape.")
        return {"error": "Not logged in", "url": url}
    
    company_data = {
        'url': url,
        'name': "",
        'industry': "",
        'headquarter': "",
        'no of employees': "",
        'website': "",
        'phone': "",
        'associated_members': "",
        'founded': "",
        'specialties': "",
        'description': "",
        'key_personnel': {
            "founder & ceo": [],
            "vice president": [],
            "cto": [],
            "hr": []
        }
    }
    
    page_source = driver.page_source
    soup = BeautifulSoup(page_source, 'lxml')
    
    try:
        name = soup.find('h1', {'class': 'org-top-card-summary__title'})
        if not name:
            name = soup.find('h1', {'class': 'top-card-layout__title'})
        
        if not name:
            name = soup.find('h1')
        
        if name:
            company_data['name'] = name.get_text().strip()
            print(f"Found company name: {company_data['name']}")
    except Exception as e:
        print(f"Error extracting company name: {e}")
    
    try:
        industry_dt = soup.find('dt', string=lambda t: t and 'Industry' in t)
        if industry_dt:
            industry_dd = industry_dt.find_next('dd')
            if industry_dd:
                company_data['industry'] = industry_dd.get_text().strip()
                print(f"Found industry from page: {company_data['industry']}")
        
        if not company_data['industry']:
            for class_name in ['org-top-card-summary-info-list__info-item', 'top-card-layout__headline']:
                industry_elem = soup.find(['div', 'h2', 'span'], {'class': class_name})
                if industry_elem:
                    company_data['industry'] = industry_elem.get_text().strip()
                    print(f"Found industry from top card: {company_data['industry']}")
                    break
    except Exception as e:
        print(f"Error extracting company industry: {e}")
    
    try:
        hq_dt = soup.find('dt', string=lambda t: t and 'Headquarters' in t)
        if hq_dt:
            hq_dd = hq_dt.find_next('dd')
            if hq_dd:
                company_data['headquarter'] = hq_dd.get_text().strip()
                print(f"Found headquarters from page: {company_data['headquarter']}")
        
        if not company_data['headquarter']:
            location_pattern = r'[\w\s-]+,\s+[\w\s-]+'
            location_elements = soup.find_all(['div', 'span'], string=lambda t: t and re.search(location_pattern, t) and not 'followers' in t.lower())
            
            for elem in location_elements:
                text = elem.get_text().strip()
                if re.search(location_pattern, text) and not text.endswith("followers") and not "industry" in text.lower():
                    company_data['headquarter'] = text
                    print(f"Found headquarters from pattern match: {company_data['headquarter']}")
                    break
    except Exception as e:
        print(f"Error extracting company headquarters: {e}")
    
    try:
        size_dt = soup.find('dt', string=lambda t: t and 'Company size' in t)
        if size_dt:
            size_dd = size_dt.find_next('dd')
            if size_dd:
                company_data['no of employees'] = size_dd.get_text().strip()
                print(f"Found employee count from page: {company_data['no of employees']}")
        
        if not company_data['no of employees']:
            employee_count_span = soup.find('span', {'class': 't-normal t-black--light link-without-visited-state link-without-hover-state'})
            if employee_count_span and 'employee' in employee_count_span.get_text().lower():
                company_data['no of employees'] = employee_count_span.get_text().strip()
                print(f"Found employee count from span: {company_data['no of employees']}")
    except Exception as e:
        print(f"Error extracting employee count: {e}")
    
    about_data = scrape_company_about_page(driver, url)
    
    for key, value in about_data.items():
        if value:
            company_data[key] = value
    
    return company_data

def extract_profile_data(card):
    """Extract profile data from a LinkedIn profile card"""
    try:
        profile_data = {}
        
        name_element = card.find('a', {'class': 'app-aware-link'}) or card.find('a', {'class': 'link-without-visited-state'})
        if name_element:
            url = name_element.get('href', '')
            profile_data['url'] = url.split('?')[0] if '?' in url else url
            
            name_span = name_element.find('span', {'class': 'org-people-profile-card__profile-title'})
            if name_span:
                profile_data['name'] = name_span.get_text().strip()
            else:
                name_div = name_element.find('div')
                if name_div:
                    profile_data['name'] = name_div.get_text().strip()
                else:
                    link_text = name_element.get_text().strip()
                    if link_text and not link_text.startswith('http') and not link_text.startswith('www'):
                        profile_data['name'] = link_text
        
        title_div = card.find('div', {'class': 'lt-line-clamp--multi-line'}) or \
                   card.find('div', {'class': 'artdeco-entity-lockup__subtitle'}) or \
                   card.find('div', {'class': 'org-people-profile-card__subtitle'})
                   
        if title_div:
            profile_data['title'] = title_div.get_text().strip()
        
        if profile_data.get('name') or profile_data.get('url'):
            return profile_data
        return None
    except Exception as e:
        print(f"Error in extract_profile_data: {e}")
        return None

def scroll_and_scrape_people(driver, all_employees):
    """Scroll through people page and scrape employee profiles"""
    scroll_count = 0
    consecutive_no_new_profiles = 0
    
    print("Starting to scrape employee profiles...")
    
    while True:
        scroll_count += 1
        print(f"Scroll attempt #{scroll_count}...")
        
        page_source = driver.page_source
        soup = BeautifulSoup(page_source, 'lxml')
        
        profile_cards = soup.find_all('li', {'class': 'grid'})
        
        if not profile_cards:
            profile_cards = soup.find_all('li', {'class': 'org-people-profiles-module__profile-item'})
            
        if not profile_cards:
            profile_cards = soup.find_all('li', {'class': 'org-people-profile-card'})
            
        if not profile_cards:
            # Try a more generic approach - find all list items with links
            all_li = soup.find_all('li')
            profile_cards = []
            for li in all_li:
                if li.find('a') and li.find('a').get('href') and '/in/' in li.find('a').get('href'):
                    profile_cards.append(li)
        
        if not profile_cards:
            print("No profile cards found on this page.")
            break
        
        print(f"Found {len(profile_cards)} profile cards on this scroll.")
        
        initial_length = len(all_employees)
        for card in profile_cards:
            try:
                profile_data = extract_profile_data(card)
                if profile_data and not any(p.get('url') == profile_data.get('url') for p in all_employees):
                    all_employees.append(profile_data)
                    if len(all_employees) % 10 == 0:
                        print(f"Collected {len(all_employees)} profiles so far...")
            except Exception as e:
                print(f"Error extracting profile: {e}")
        
        new_profiles_found = len(all_employees) - initial_length
        print(f"Found {new_profiles_found} new profiles in this scroll.")
        
        if new_profiles_found == 0:
            consecutive_no_new_profiles += 1
            print(f"No new profiles found for {consecutive_no_new_profiles} consecutive scrolls.")
        else:
            consecutive_no_new_profiles = 0
        
        if consecutive_no_new_profiles >= 3:
            print("No new profiles for 3 consecutive scrolls. Ending search.")
            break
        
        try:
            show_more_button = driver.find_element(By.XPATH, "//button[contains(., 'Show more')]")
            if not show_more_button.is_displayed() or not show_more_button.is_enabled():
                print("'Show more' button is not clickable. Ending search.")
                break
                
            print("Clicking 'Show more' button...")
            driver.execute_script("arguments[0].click();", show_more_button)
            sleep(3)
        except Exception as e:
            print(f"No 'Show more' button found ({str(e)}). Scrolling down instead.")
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            sleep(3)
            
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight * 0.8);")
            sleep(1)
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            sleep(3)
    
    print(f"Finished scraping people. Found a total of {len(all_employees)} employee profiles.")

def identify_key_personnel(all_employees, company_data):
    """Identify key personnel from employee profiles"""
    role_keywords = {
        "founder & ceo": ["founder", "ceo", "chief executive officer", "co-founder", "founder & ceo", "cofounder"],
        "vice president": ["vice president", "vp", "executive vice president", "senior vice president", "evp", "svp"],
        "cto": ["cto", "chief technology officer", "chief technical officer", "vp of technology", 
               "vp of engineering", "head of technology", "head of engineering", "tech lead"],
        "hr": ["hr", "human resources", "people and culture", "people operations", "talent", 
              "recruiting", "people officer", "chro", "chief human resources"]
    }
    
    role_counts = {role: 0 for role in role_keywords}
    
    print("\nIdentifying key personnel by role...")
    for employee in all_employees:
        if "title" in employee and employee["title"]:
            title_lower = employee["title"].lower()
            
            for role, keywords in role_keywords.items():
                try:
                    if any(keyword in title_lower for keyword in keywords):
                        person_copy = {k: v for k, v in employee.items() if k != 'role'}
                        
                        if role not in company_data["key_personnel"]:
                            company_data["key_personnel"][role] = []
                        
                        if not any(p.get('url') == person_copy.get('url') for p in company_data["key_personnel"][role]):
                            company_data["key_personnel"][role].append(person_copy)
                            role_counts[role] += 1
                        break
                except Exception as e:
                    print(f"ERROR: {e}")
    
    for role, count in role_counts.items():
        print(f"Found {count} {role} personnel")
    
    return role_counts

def scrape_company_people(driver, company_url, company_data):
    """Scrape people data for a LinkedIn company"""
    # First try the /people/ page
    people_url = f"{company_url}/people/"
    driver.get(people_url)
    print(f"Navigating to company's people page: {people_url}")
    sleep(3)
    
    if "login" in driver.current_url:
        print("Session expired or not logged in. Cannot scrape people.")
        return company_data
    
    all_employees = []
    
    # Try to get employee names from the people page
    print("Attempting to scrape from /people/ page...")
    scroll_and_scrape_people(driver, all_employees)
    
    # If we didn't find any employees, try the main company page as fallback
    if len(all_employees) == 0:
        print("No employees found on /people/ page. Trying main company page...")
        driver.get(company_url)
        sleep(3)
        
        # Look for "See all employees" link and click it if found
        try:
            see_all_link = driver.find_element(By.XPATH, "//a[contains(text(), 'See all')]")
            if see_all_link:
                print("Found 'See all' link. Clicking it...")
                driver.execute_script("arguments[0].click();", see_all_link)
                sleep(3)
                
                # Now try scraping again
                scroll_and_scrape_people(driver, all_employees)
        except Exception as e:
            print(f"Couldn't find 'See all' link: {e}")
    
    # If we still didn't find any employees, try one more approach
    if len(all_employees) == 0:
        print("Still no employees found. Trying direct search...")
        
        # Get company name
        company_name = company_data.get('name', '')
        if company_name:
            search_url = f"https://www.linkedin.com/search/results/people/?keywords={company_name.replace(' ', '%20')}"
            driver.get(search_url)
            print(f"Searching for employees with company name: {company_name}")
            sleep(3)
            
            # Try scraping again
            scroll_and_scrape_people(driver, all_employees)
    
    role_counts = identify_key_personnel(all_employees, company_data)
    
    print("\nPersonnel Summary:")
    print(f"Total profiles found: {len(all_employees)}")
    for role, count in role_counts.items():
        if count > 0:
            print(f"- {role}: {count} people")
    
    return company_data

def save_to_json(data, filename):
    """Save data to a JSON file"""
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
    print(f"Data saved to {filename}")

def scrape_company(company_url):
    """Scrape a LinkedIn company page and save the data"""
    global driver, is_logged_in, is_scraping, current_job
    
    is_scraping = True
    current_job = {"url": company_url, "start_time": int(time.time())}
    
    try:
        if not is_logged_in:
            print("Not logged in. Attempting to log in...")
            if not ensure_login():
                is_scraping = False
                current_job = None
                return {"error": "Failed to log in", "status": "error"}
        
        print("\n" + "="*80)
        print(f"STARTING SCRAPE FOR: {company_url}")
        print("="*80)
        
        company_data = scrape_company_basics(driver, company_url)
        
        if "error" in company_data:
            is_scraping = False
            current_job = None
            return {"error": company_data["error"], "status": "error", "url": company_url}
        
        company_data = scrape_company_people(driver, company_url, company_data)
        
        company_name = company_data.get('name')
        if not company_name or company_name.strip() == "":
            company_name = "company"
        company_name = company_name.replace(" ", "_").lower()
        filename = f"data/{company_name}_data.json"
        
        save_to_json(company_data, filename)
        
        # Count personnel
        total_personnel = 0
        for role, personnel in company_data["key_personnel"].items():
            total_personnel += len(personnel)
        
        result = {
            "status": "success",
            "company_name": company_data.get('name', 'Unknown'),
            "url": company_url,
            "file_path": filename,
            "personnel_count": total_personnel,
            "data": company_data
        }
        
        return result
    except Exception as e:
        print(f"Error in scrape_company: {e}")
        return {
            "status": "error",
            "message": str(e),
            "url": company_url
        }
    finally:
        # Always clean up resources in finally block
        is_scraping = False
        current_job = None

# API Routes - Only modify the login endpoint
@app.route('/status', methods=['GET'])
def status():
    """Check current scraping status"""
    try:
        global is_scraping, current_job, is_logged_in
        
        if current_job:
            current_job["duration"] = int(time.time()) - current_job["start_time"]
        
        return jsonify({
            "logged_in": is_logged_in,
            "is_scraping": is_scraping,
            "current_job": current_job
        })
    except Exception as e:
        print(f"Error in status endpoint: {e}")
        return jsonify({"error": str(e), "status": "error"})

@app.route('/login', methods=['GET'])
def login():
    """Auto login endpoint"""
    global is_logged_in, driver
    
    if is_logged_in:
        return jsonify({
            "status": "already_logged_in",
            "message": "Already logged in to LinkedIn"
        })
    
    try:
        if auto_login():
            return jsonify({
                "status": "success",
                "message": "Successfully logged in to LinkedIn"
            })
        else:
            return jsonify({
                "status": "error",
                "message": "Failed to log in to LinkedIn"
            }), 500
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"Error during login: {str(e)}"
        }), 500

@app.route('/company', methods=['GET'])
def company_endpoint():
    """Endpoint to start scraping a LinkedIn company"""
    global is_scraping, is_logged_in, driver, current_job, job_results
    
    company_url = request.args.get('url')
    force = request.args.get('force', 'false').lower() == 'true'
    
    if not company_url:
        return jsonify({
            "status": "error",
            "message": "Missing URL parameter. Use /company?url=https://www.linkedin.com/company/example"
        }), 400
        
    # Clean up the URL to ensure it's a LinkedIn company page
    if not company_url.startswith('https://www.linkedin.com/company/'):
        # Try to extract company name if full URL provided
        if 'linkedin.com/company/' in company_url:
            parts = company_url.split('linkedin.com/company/')
            if len(parts) > 1:
                company_name = parts[1].split('/')[0].split('?')[0]
                company_url = f"https://www.linkedin.com/company/{company_name}"
        else:
            return jsonify({
                "status": "error", 
                "message": "Invalid URL. Please provide a URL in the format https://www.linkedin.com/company/company-name"
            }), 400
    
    # Check if we're already scraping
    if is_scraping and not force:
        return jsonify({
            "status": "busy",
            "message": "Already scraping another company. Add ?force=true to override.",
            "current_job": current_job
        }), 409
    
    # Start the scraping process
    def run_scrape():
        global job_results, is_scraping, current_job, driver, is_logged_in
        
        # Reset state if force=true
        if force and is_scraping:
            print("Forced scraping requested. Resetting previous scraping job.")
            try:
                if driver:
                    driver.quit()
                    driver = None
            except Exception as e:
                print(f"Error closing driver during force reset: {e}")
            finally:
                is_scraping = False
                current_job = None
                is_logged_in = False
        
        try:
            # Make sure we're logged in first
            print("\n" + "="*80)
            print(f"STARTING LINKEDIN SCRAPE FOR: {company_url}")
            print("="*80)
            
            if not is_logged_in or not driver:
                print("LinkedIn login required before scraping")
                login_success = auto_login()
                if not login_success:
                    error_msg = "Failed to log in to LinkedIn. Please try running the scraper again."
                    print(f"ERROR: {error_msg}")
                    job_results[company_url] = {
                        "status": "error",
                        "message": error_msg,
                        "url": company_url
                    }
                    is_scraping = False
                    current_job = None
                    return
            else:
                print("Already logged in, continuing with scraping")
            
            # Execute the actual scraping
            result = scrape_company(company_url)
            
            # Store the result
            job_results[company_url] = result
            
            # Delete the JSON file after storing the results
            if result["status"] == "success" and "file_path" in result:
                try:
                    file_path = result["file_path"]
                    if os.path.exists(file_path):
                        os.remove(file_path)
                        print(f"✅ Successfully deleted JSON file: {file_path}")
                    else:
                        print(f"Warning: JSON file not found for deletion: {file_path}")
                except Exception as e:
                    print(f"Error deleting JSON file: {e}")
            
            # Print the final result in terminal to confirm completion
            print("\n" + "="*80)
            if result["status"] == "success":
                print(f"✅ SCRAPING COMPLETED SUCCESSFULLY FOR: {company_url}")
                
                # Print summary of results
                print("\n📋 SUMMARY:")
                company_data = result["data"]
                print(f"Company: {company_data.get('name', 'Unknown')}")
                print(f"Industry: {company_data.get('industry', 'Not found')}")
                print(f"Location: {company_data.get('headquarter', 'Not found')}")
                print(f"Size: {company_data.get('company_size', 'Not found')}")
                print("\nKey Personnel:")
                for role, personnel in company_data["key_personnel"].items():
                    if personnel:
                        print(f"- {role.title()}: {len(personnel)} found")
            else:
                print(f"❌ SCRAPING FAILED FOR: {company_url}")
                print(f"Error: {result.get('message', 'Unknown error')}")
            
            print("="*80)
            
        except Exception as e:
            print("\n" + "="*80)
            print(f"ERROR SCRAPING COMPANY: {company_url}")
            print(f"Error details: {str(e)}")
            print("="*80)
            
            # Create error result
            error_result = {
                "status": "error",
                "company_name": "Unknown",
                "url": company_url,
                "error": str(e),
                "message": "Scraping failed due to an error."
            }
            
            # Store the error result
            job_results[company_url] = error_result
        finally:
            # Update state flags
            is_scraping = False
            if current_job:
                current_job["end_time"] = int(time.time())
                if current_job.get("start_time"):
                    current_job["duration"] = current_job["end_time"] - current_job["start_time"]
            current_job = None
            
            print("\n🏁 Scraping job complete.")
            print("You can close the browser window if you're finished.")
    
    thread = Thread(target=run_scrape)
    thread.daemon = True
    thread.start()
    
    return jsonify({
        "status": "started",
        "message": f"Scraping job started for {company_url}. The system will attempt to log in automatically using saved credentials.",
        "url": company_url
    })

@app.route('/results', methods=['GET'])
def get_results():
    """Get results of completed scraping jobs"""
    url = request.args.get('url')
    
    if url:
        # Return results for a specific URL
        if url in job_results:
            return jsonify(job_results[url])
        else:
            return jsonify({
                "status": "error",
                "message": f"No results found for URL: {url}"
            }), 404
    else:
        # Return list of all URLs with results
        return jsonify({
            "status": "success",
            "urls": list(job_results.keys())
        })
        
@app.route('/reset', methods=['GET'])
def reset_scraper():
    """Reset the scraper's state completely"""
    global is_scraping, is_logged_in, driver, current_job
    
    try:
        # Properly close the driver if it exists
        if driver:
            driver.quit()
    except Exception as e:
        print(f"Error closing driver during reset: {e}")
    
    # Reset all global variables
    is_scraping = False
    is_logged_in = False
    driver = None
    current_job = None
    
    return jsonify({"status": "success", "message": "Scraper state completely reset"})

@app.route('/proxy/salesql', methods=['GET'])
def proxy_salesql():
    """
    Proxy requests to SalesQL API to avoid CORS issues
    
    Request:
    GET /proxy/salesql?linkedin_url=https://www.linkedin.com/in/abhishek-gupta-ag33
    
    Response:
    SalesQL API response
    """
    try:
        linkedin_url = request.args.get('linkedin_url')
        if not linkedin_url:
            return jsonify({
                "status": "error",
                "message": "Missing linkedin_url parameter"
            }), 400
            
        # SalesQL API key
        api_key = "JdKM5dJJfb7mZCjxQvhNhSBFbEjShRlA"
        
        # Clean up the LinkedIn URL to ensure it's valid
        linkedin_url = linkedin_url.strip()
        if not linkedin_url.startswith('http'):
            linkedin_url = 'https://' + linkedin_url
            
        # Remove any trailing slashes
        linkedin_url = linkedin_url.rstrip('/')
        
        print(f"Making SalesQL API request for: {linkedin_url}")
        
        # Make request to SalesQL API with improved error handling
        import requests
        try:
            response = requests.get(
                "https://api-public.salesql.com/v1/persons/enrich",
                params={
                    "linkedin_url": linkedin_url,
                    "api_key": api_key
                },
                timeout=30
            )
            
            # Try to parse JSON response
            response_data = response.json()
            print(f"SalesQL API response status code: {response.status_code}")
            
            # Return the response data
            return jsonify(response_data)
            
        except requests.exceptions.HTTPError as http_err:
            status_code = getattr(http_err.response, 'status_code', 500)
            error_message = f"HTTP error from SalesQL API: {http_err}"
            
            print(f"SalesQL API HTTP error: {status_code} - {error_message}")
            
            return jsonify({
                "status": "error",
                "code": status_code,
                "message": "Person not found in SalesQL database" if status_code == 404 else error_message
            }), 200  # Return 200 so frontend can handle it gracefully
            
        except requests.exceptions.RequestException as req_err:
            error_type = type(req_err).__name__
            print(f"SalesQL API request error ({error_type}): {req_err}")
            
            return jsonify({
                "status": "error",
                "message": f"SalesQL API request failed: {req_err}"
            }), 200
            
        except ValueError as json_err:
            print(f"SalesQL API JSON parsing error: {json_err}")
            
            # If we reach this point, the request succeeded but returned invalid JSON
            # Try to get the raw response text
            raw_text = getattr(response, 'text', 'No response text available')
            
            return jsonify({
                "status": "error",
                "message": "Invalid JSON response from SalesQL API",
                "raw_response": raw_text[:500]  # Include part of raw response for debugging
            }), 200
            
    except Exception as e:
        print(f"Error proxying SalesQL request: {e}")
        return jsonify({
            "status": "error",
            "message": f"Failed to proxy SalesQL request: {str(e)}"
        }), 200  # Return 200 so frontend can handle it gracefully
        
@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve(path):
    if path != "" and os.path.exists("static/" + path):
        return send_from_directory("static", path)
    else:
        return send_from_directory("static", "index.html")

# Start the Flask server when run directly
if __name__ == '__main__':
    try:
        # Create necessary directories
        os.makedirs("data", exist_ok=True)
        os.makedirs("static", exist_ok=True)  # Ensure static directory exists
        
        # Create a basic index.html if it doesn't exist
        if not os.path.exists("static/index.html"):
            with open("static/index.html", "w") as f:
                f.write("<html><body><h1>LinkedIn Company Scraper</h1><p>Use the API endpoints to interact with the scraper.</p></body></html>")
        
        # Start the Flask server
        print("Starting LinkedIn Company Scraper...")
        print("Access the web interface via API endpoints at http://localhost:5003")
        app.run(host='0.0.0.0', port=5003, debug=True)
    except KeyboardInterrupt:
        print("\nShutting down the server...")
        if driver:
            driver.quit()
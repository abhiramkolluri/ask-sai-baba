import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException
from time import sleep
import json

url = "https://saispeaks.sathyasai.org/discourses/collection=Sri%20Sathya%20Sai%20Speaks%2C%20Vol%2043%20%282010%29"


file = 'options.json'

# Read the JSON file
with open(file, 'r') as f:
    options = json.load(f)
    
results = []
for option in options:
    print(option)
# Configure the Selenium webdriver
# options = webdriver.ChromeOptions()
# options.add_argument('--headless')  # Run Chrome in headless mode, without opening a browser window
# options.add_argument('--disable-gpu')
    driver = webdriver.Chrome()

    # Load the page
    driver.get(url)

    selection_input  = driver.find_element(By.CLASS_NAME,"discourse-collection")
    selection_input.send_keys(option['text'])


    button = driver.find_element(By.ID,"edit-discourse-search-submit")
    button.click()
    # Wait for the page to fully load (adjust wait time as needed)
    driver.implicitly_wait(20)
    wait = WebDriverWait(driver, 10)
    discourse_listings = wait.until(EC.presence_of_all_elements_located((By.CLASS_NAME, 'discourse-listing')))

    # print(driver.find_element(By.CLASS_NAME,"discourse-listings"))

    # Extract the HTML content after JavaScript execution
    html_content = driver.page_source
    # html_content = driver.find_element(By.CLASS_NAME,"discourse-listings")

    # print(html_content)

    sleep(2)

    # Close the Selenium webdriver

    # # Parse the HTML content with Beautiful Soup
    soup = BeautifulSoup(html_content, 'html.parser')

    # Find all elements with class 'discourse-listing'
    discourse_listings = soup.find_all(class_='discourse-listing')
    # print(len(discourse_listings))
    # Iterate over each discourse listing
    for discourse_listing in discourse_listings:
        # Find elements with class 'title' and 'content' within the discourse listing
        title = discourse_listing.find(class_='title')
        content = discourse_listing.find(class_='content')
        collection = discourse_listing.find(class_='collection')
        date = discourse_listing.find(class_='date')
        discoursenum = collection.find(class_='discourse-no')
        res = {
                "title":title.get_text(strip=True),
                "Content:": content.get_text(strip=True),
                "collection:": collection.get_text(strip=True),
                "date:": date.get_text(strip=True),
                "discourse_number:": discoursenum.get_text(strip=True) if not discoursenum == None else "" ,
                }
        results.append(res)


    ## find the next page element if it exists
    try:
        nextpage = driver.find_element(By.CSS_SELECTOR,'li.next > a')
        nextpage.click()

        wait = WebDriverWait(driver, 10)
        previous = wait.until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, 'li.prev > a')))
        html_content = driver.page_source

        sleep(2)

        soup = BeautifulSoup(html_content, 'html.parser')

        discourse_listings = soup.find_all(class_='discourse-listing')

        for discourse_listing in discourse_listings:

            title = discourse_listing.find(class_='title')
            content = discourse_listing.find(class_='content')
            collection = discourse_listing.find(class_='collection')
            date = discourse_listing.find(class_='date')
            discoursenum = collection.find(class_='discourse-no')
            res = {
                "title":title.get_text(strip=True),
                "Content:": content.get_text(strip=True),
                "collection:": collection.get_text(strip=True),
                "date:": date.get_text(strip=True),
                "discourse_number:": discoursenum.get_text(strip=True) if not discoursenum == None else "" ,
                }
            results.append(res)
            print(len(results))
    except NoSuchElementException:
        driver.quit()
        print("there is no next page")



output_file = 'data.json'
print(len(results))
# Write the data to the JSON file
with open(output_file, 'w') as f:
    json.dump(results, f, indent=4)
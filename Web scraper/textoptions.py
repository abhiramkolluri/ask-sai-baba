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

# Configure the Selenium webdriver
options = webdriver.ChromeOptions()
driver = webdriver.Chrome()

# Load the page
driver.get(url)



html_content = driver.page_source
sleep(2)

# # Parse the HTML content with Beautiful Soup
soup = BeautifulSoup(html_content, 'html.parser')

# Find all elements with class 'discourse-listing'
select = soup.select('.ql-discourse-collection')
options = select[0].find_all('option')

results = []
# Iterate over each options
for option in options:
    text = option.get_text(strip=True)
    print(text)
    results.append({
        "text":text
    })

output_file = 'options.json'

# Write the data to the JSON file
with open(output_file, 'w') as f:
    json.dump(results, f, indent=4)

driver.quit()

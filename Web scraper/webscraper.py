import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.common.action_chains import ActionChains

from time import sleep
import json

url = "https://saispeaks.sathyasai.org/discourses/collection=Sri%20Sathya%20Sai%20Speaks%2C%20Vol%2043%20%282010%29"
file = 'options.json'

# Read the JSON file
with open(file, 'r') as f:
    options = json.load(f)

results = []

# Configure the Selenium webdriver
driver = webdriver.Chrome()

# Load the page
driver.get(url)

# Loop through all of the options that exist for the religious text
for option in options:
    selection_input = driver.find_element(
        By.CLASS_NAME, "discourse-collection")
    selection_input.clear()
    selection_input.send_keys(option['text'])

    # Click the submit button
    button = driver.find_element(By.ID, "edit-discourse-search-submit")

    # Scroll the submit button into view
    actions = ActionChains(driver)
    actions.move_to_element(button).perform()

    # Wait for the submit button to be clickable
    wait = WebDriverWait(driver, 10)
    wait.until(EC.element_to_be_clickable(
        (By.ID, "edit-discourse-search-submit")))

    button.click()

    # Wait for the page to fully load
    sleep(5)  # Wait for 5 seconds for the page to load

    while True:  # Loop until there's no next page
        # Extract the HTML content after JavaScript execution
        html_content = driver.page_source

        # Parse the HTML content with Beautiful Soup
        soup = BeautifulSoup(html_content, 'html.parser')

        # Find all elements with class 'discourse-listing'
        discourse_listings = soup.find_all(class_='discourse-listing')

        # Iterate over each discourse listing
        for discourse_listing in discourse_listings:
            title = discourse_listing.find(class_='title').get_text(strip=True)
            content = discourse_listing.find(
                class_='content').get_text(strip=True)
            collection = discourse_listing.find(
                class_='collection').get_text(strip=True)
            date = discourse_listing.find(class_='date').get_text(strip=True)
            # Extracting discourse number from collection
            discoursenum = collection.split()[-1]
            res = {
                "title": title,
                "Content": content,
                "collection": collection,
                "date": date,
                "discourse_number": discoursenum if discoursenum.isdigit() else "",
            }
            results.append(res)

        # Look for the next page link
        try:
            next_page = driver.find_element(By.CSS_SELECTOR, 'li.next > a')
            next_page.click()
            sleep(5)  # Wait for 5 seconds after clicking next page link
        except NoSuchElementException:
            print("No next page found for option:", option['text'])
            break  # Exit the loop if there's no next page for the current option

# Quit the driver after processing all options
driver.quit()

# Write the data to the JSON file
output_file = 'data.json'
with open(output_file, 'w') as f:
    json.dump(results, f, indent=4)

print("Data saved successfully.")

# Print the results to verify
print(results)

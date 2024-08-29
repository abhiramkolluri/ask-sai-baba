from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.common.action_chains import ActionChains
from html_parser import strip_tags
from time import sleep
import json

base_url = "https://saispeaks.sathyasai.org"
url = f"{base_url}/discourses/collection=Sri%20Sathya%20Sai%20Speaks%2C%20Vol%2043%20%282010%29"
file = 'options.json'

# Read the JSON file
with open(file, 'r') as f:
    options = json.load(f)

results = []
clicked_links = []  # List to store clicked links

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
    wait = WebDriverWait(driver, 20)
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
            # Extracting title and link
            title_element = discourse_listing.find(class_='title')
            title = title_element.get_text(strip=True)
            link = title_element.find('a')['href']

            # Combine the base URL with the relative link
            full_link = base_url + link

            # Append the clicked link to the list
            clicked_links.append({"title": title, "link": full_link})

            # Open the combined link in a new tab
            driver.execute_script(
                "window.open('{}', '_blank');".format(full_link))
            # Switch to the newly opened tab
            driver.switch_to.window(driver.window_handles[1])

            # Now you are on the linked page, you can extract data as before
            message_panel = driver.find_element(
                By.CLASS_NAME, "section-content")

            # Locate all message elements within the panel
            messages = message_panel.find_elements(
                By.XPATH, './/*[self::div[@class="discourse_para"] or self::div[contains(@class, "discourse_section")] or self::div[contains(@class, "callout")] or self::div[@class="discourse_editor_note"]]')

            # List to store message content for all messages
            message_contents = []

            # Check if there are no messages
            if not messages:
                print("No text found on the current discourse collection . Skipping.")
            else:
                # Iterate through each message element
                for message_element in messages:
                    # Extract and print the message content
                    # message_content = strip_tags(
                    # message_element.get_attribute("innerHTML"))
                    message_content = strip_tags(message_element.text)
                    print(message_content)
                    # Append message content to the list
                    message_contents.append(message_content)

            # Combine message content for all messages into a single string
            message_content = "\n".join(message_contents)

            # Close the current tab and switch back to the main tab
            driver.close()
            driver.switch_to.window(driver.window_handles[0])
            # Add a brief delay to ensure the main page is fully loaded
            sleep(2)

            # Extract data specific to each discourse listing
            collection = discourse_listing.find(
                class_='collection').get_text(strip=True)
            date = discourse_listing.find(class_='date').get_text(strip=True)
            discoursenum = collection.split()[-1]

            res = {
                "title": title,
                "content": message_content,
                "collection": collection,
                "date": date,
                "discourse_number": discoursenum if discoursenum.isdigit() else "",
                "link": full_link  # Include the link in the dictionary
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
output_file = 'data_all.json'
with open(output_file, 'w') as f:
    json.dump(results, f, indent=4)

# Write clicked links to the data file
with open('data.json', 'w') as f:
    json.dump(clicked_links, f, indent=4)

print("Data saved successfully.")

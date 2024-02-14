import requests
from bs4 import BeautifulSoup
from selenium import webdriver

# Path to the chromedriver executable
# CHROMEDRIVER_PATH = r'chromedriver.exe'

# URL of the page with dynamically generated content
url = "https://saispeaks.sathyasai.org/discourses/collection=Sri%20Sathya%20Sai%20Speaks%2C%20Vol%2043%20%282010%29"

# Configure the Selenium webdriver
options = webdriver.ChromeOptions()
options.add_argument('--headless')  # Run Chrome in headless mode, without opening a browser window
cService = webdriver.ChromeService(executable_path='chromedriver')
driver = webdriver.Chrome()

# Load the page
driver.get(url)

# Wait for the page to fully load (adjust wait time as needed)
driver.implicitly_wait(10)

# Extract the HTML content after JavaScript execution
html_content = driver.page_source

# Close the Selenium webdriver
driver.quit()

# Parse the HTML content with Beautiful Soup
soup = BeautifulSoup(html_content, 'html.parser')

# Find all elements with class 'discourse-listing'
discourse_listings = soup.find_all(class_='discourse-listing')

# Iterate over each discourse listing
for discourse_listing in discourse_listings:
    # Find elements with class 'title' and 'content' within the discourse listing
    titles = discourse_listing.find_all(class_='title')
    contents = discourse_listing.find_all(class_='content')
    
    # Print the text of each title and content
    for title, content in zip(titles, contents):
        print("Title:", title.get_text(strip=True))
        print("Content:", content.get_text(strip=True))
        print()

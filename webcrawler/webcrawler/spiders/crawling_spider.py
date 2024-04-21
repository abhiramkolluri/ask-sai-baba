import scrapy
from scrapy_splash import SplashRequest


class CrawlingSpider(scrapy.Spider):
  name = "sathyacrawler"
  allowed_domains = ["saispeaks.sathyasai.org"]
  visited_urls_file = 'visited_urls.txt'
  start_url = "https://saispeaks.sathyasai.org/discourses/?collection=Sri%20Sathya%20Sai%20Speaks%2C%20Vol%2035%20%282002%29&page={}"

  def start_requests(self):
    yield SplashRequest(self.start_url, self.parse, endpoint='render.html', args={'wait': 5})

  def parse(self, response):
    self.logger.info(f"Processing page: {response.url}")

    # Extract data from the current page
    discourse_listings = response.css('.discourse-listing')
    for discourse_listing in discourse_listings:
      yield {
          "title": discourse_listing.css('.title a::text').get(),
          "content": discourse_listing.css('.content::text').get(),
      }

      # Extract PDF URLs and yield requests to download them
      pdf_urls = response.css('a[href$=".pdf"]::attr(href)').getall()
      for pdf_url in pdf_urls:
        yield scrapy.Request(pdf_url, callback=self.save_pdf)

    # Follow pagination links
    next_page_link = response.css(
        '.pagination-links .next a::attr(href)').get()
    if next_page_link:
      self.logger.info(f"Following next page link: {next_page_link}")
      yield SplashRequest(response.urljoin(next_page_link), self.parse, endpoint='render.html', args={'wait': 5})
    else:
      self.logger.info("No next page link found.")

  def save_pdf(self, response):
    # Save PDF files to a directory
    filename = response.url.split('/')[-1]
    with open(filename, 'wb') as f:
      f.write(response.body)

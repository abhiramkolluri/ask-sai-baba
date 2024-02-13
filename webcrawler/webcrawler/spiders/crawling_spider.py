from scrapy.spiders import CrawlSpider, Rule
from scrapy.linkextractors import LinkExtractor



class CrawlingSpider(CrawlSpider):
    name = "sathyacrawler"
    allowed_domains = ["sathyasai.org"]
    start_urls = ["https://saispeaks.sathyasai.org/discourses/?collection=Sri%20Sathya%20Sai%20Speaks%2C%20Vol%204%20%281964%29"]


    # rules = (
    #     Rule(
    #         LinkExtractor(allow='discourses/.*'),
    #         # callback='parse_page',
    #         follow=True
    #     ),
    # )
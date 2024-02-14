from scrapy.spiders import CrawlSpider, Rule
from scrapy.linkextractors import LinkExtractor
from scrapy_splash import SplashRequest


class CrawlingSpider(CrawlSpider):
    name = "sathyacrawler"
    allowed_domains = ["sathyasai.org"]
    start_urls = ["https://www.sathyasai.org/resources/ebooks/sathya-sai-speaks"]


    # def start_requests(self):
    #     for url in self.start_urls:
    #         yield SplashRequest(url, args={'wait': 2})

    

    rules = (
        Rule(
            LinkExtractor(allow='discourses/.*'),
            callback='parse',
            # process_request='splash_request',
        ),
    )


    def splash_request(self, request):
        return SplashRequest(
            request.url,
            callback=request.callback,
            endpoint='render.html',
            args={'wait': 3.5},
        )
    def parse(self,response):
        discourse_listings = response.css('.discourse-listings')
        for discourse_listing in discourse_listings:
            yield {
                "title":discourse_listing.css('.discourse-listing > .title::text').get(),
                "content":discourse_listing.css('.discourse-listing > .content::text').get(),
            }
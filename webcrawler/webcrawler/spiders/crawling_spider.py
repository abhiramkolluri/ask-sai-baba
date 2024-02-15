# import scrapy
from scrapy.spiders import CrawlSpider, Rule
from scrapy.linkextractors import LinkExtractor
from scrapy_splash import SplashRequest


class CrawlingSpider(CrawlSpider):
    name = "sathyacrawler"
    allowed_domains = ["sathyasai.org"]
    visited_urls_file = 'visited_urls.txt' 
    # start_urls = ["https://www.sathyasai.org/resources/ebooks/sathya-sai-speaks"]
    
    start_urls = ["https://saispeaks.sathyasai.org/discourses/?collection=Sri%20Sathya%20Sai%20Speaks%2C%20Vol%2043%20%282010%29"]

    def start_requests(self):
        for url in self.start_urls:
            yield SplashRequest(url,self.parse,endpoint='render.html',args={'wait':5})
        # url = "https://saispeaks.sathyasai.org/discourses/?collection=Sri%20Sathya%20Sai%20Speaks%2C%20Vol%2043%20%282010%29"
        # yield scrapy.Request(url=url,meta={'playwright':True})

    

    # rules = (
    #     Rule(
    #         LinkExtractor(allow='discourses/.*'),
    #         # callback='parse',
    #         # process_request='splash_request',
    #     ),
    # )

    # def splash_request(self, request):
    #     return SplashRequest(
    #         request.url,
    #         callback=request.callback,
    #         endpoint='render.html',
    #         args={'wait': 3.5},
    #     )

    # def parse(self, response):
    #     # url = 'http://www.licitor.com/ventes-judiciaires-immobilieres/tgi-fontainebleau/mercredi-15-juin-2016.html'
    #     yield SplashRequest(url=response.url, callback=self.parse_item, args={'wait': 0.5})

    def parse(self,response):
        discourse_listings = response.css('.discourse-listings')
        for discourse_listing in discourse_listings:
            yield {
                "title":discourse_listing.css('.discourse-listing > .title::text').get(),
                "content":discourse_listing.css('.discourse-listing > .content::text').get(),
            }
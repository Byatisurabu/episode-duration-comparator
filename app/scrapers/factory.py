# app/scrapers/factory.py  или в конец main.py
from app.scrapers.imdb import ImdbScraper
from app.scrapers.amediateka import AmediatekaScraper
from app.scrapers.base import ScraperFactory

ScraperFactory.register("imdb", ImdbScraper)
ScraperFactory.register("amediateka", AmediatekaScraper)
# app/scrapers/factory.py  или в конец main.py
from app.scrapers.amediateka import AmediatekaScraper
from app.scrapers.base import ScraperFactory
from app.scrapers.imdb import ImdbScraper

ScraperFactory.register("imdb", ImdbScraper)
ScraperFactory.register("amediateka", AmediatekaScraper)

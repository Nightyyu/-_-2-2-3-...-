from flask import Flask, jsonify, request
import requests
from bs4 import BeautifulSoup
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta
import sqlite3
import logging
import os
import re

app = Flask(__name__)

# Configuração de logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Inicialização do banco SQLite
def init_db():
    with sqlite3.connect('stock_data.db') as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS stock (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                name TEXT NOT NULL,
                stock INTEGER,
                price INTEGER,
                last_updated TEXT
            )
        ''')
        conn.commit()

def save_to_db(category, items, last_updated):
    with sqlite3.connect('stock_data.db') as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM stock WHERE category = ?', (category,))
        for item in items:
            cursor.execute('''
                INSERT INTO stock (category, name, stock, price, last_updated)
                VALUES (?, ?, ?, ?, ?)
            ''', (category, item['name'], item['stock'], item.get('price', 0), last_updated))
        conn.commit()

def load_from_db(category=None):
    with sqlite3.connect('stock_data.db') as conn:
        cursor = conn.cursor()
        if category:
            cursor.execute('SELECT name, stock, price, last_updated FROM stock WHERE category = ?', (category,))
            rows = cursor.fetchall()
            return [{'name': row[0], 'stock': row[1], 'price': row[2]} for row in rows], rows[0][3] if rows else None
        else:
            data = {'seeds': [], 'gear': [], 'egg_shop': [], 'honey': [], 'cosmetics': [], 'last_updated': None}
            for cat in ['seeds', 'gear', 'egg_shop', 'honey', 'cosmetics']:
                cursor.execute('SELECT name, stock, price, last_updated FROM stock WHERE category = ?', (cat,))
                rows = cursor.fetchall()
                data[cat] = [{'name': row[0], 'stock': row[1], 'price': row[2]} for row in rows]
                if rows and not data['last_updated']:
                    data['last_updated'] = rows[0][3]
            return data

def parse_update_time(time_text):
    time_text = time_text.lower().strip()
    pattern = r'(?:(\d+)h\s*)?(?:(\d+)m\s*)?(?:(\d+)s)?'
    match = re.search(pattern, time_text)
    if not match:
        return 300  # padrão 5 minutos
    hours = int(match.group(1)) if match.group(1) else 0
    minutes = int(match.group(2)) if match.group(2) else 0
    seconds = int(match.group(3)) if match.group(3) else 0
    total_seconds = hours * 3600 + minutes * 60 + seconds
    return max(total_seconds, 30)

def scrape_stock():
    url = 'https://vulcanvalues.com/grow-a-garden/stock'
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Referer": "https://vulcanvalues.com/",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }
    last_updated = datetime.now().isoformat()
    next_update_times = {}

    try:
        session = requests.Session()
        response = session.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        new_data = {
            'seeds': [],
            'gear': [],
            'egg_shop': [],
            'honey': [],
            'cosmetics': []
        }

        stock_grid = soup.find('div', class_='grid grid-cols-1 md:grid-cols-3 gap-6 px-6 text-left max-w-screen-lg mx-auto')
        if not stock_grid:
            stock_grid = soup.find('div', class_='grid')
        if not stock_grid:
            stock_grid = soup.find('main') or soup.find('section')
        if not stock_grid:
            logger.error("Estrutura principal para scraping não encontrada")
            return

        for section in stock_grid.find_all('div'):
            h2 = section.find('h2')
            if not h2:
                continue

            category = h2.text.strip().lower()

            update_time_text = ""
            update_paragraph = section.find('p', string=re.compile(r'UPDATES IN:', re.IGNORECASE))
            if not update_paragraph:
                for p in section.find_all(['p', 'div', 'span']):
                    if p.get_text() and 'updates in:' in p.get_text().lower():
                        update_time_text = p.get_text()
                        break
            else:
                update_time_text = update_paragraph.get_text()

            if update_time_text:
                time_match = re.search(r'updates in:\s*(.+)', update_time_text.lower())
                if time_match:
                    time_str = time_match.group(1).strip()
                    update_seconds = parse_update_time(time_str)
                else:
                    update_seconds = 300
            else:
                update_seconds = 300

            if 'gear' in category:
                category_key = 'gear'
            elif 'egg' in category:
                category_key = 'egg_shop'
            elif 'seeds' in category:
                category_key = 'seeds'
            elif 'honey' in category:
                category_key = 'honey'
            elif 'cosmetics' in category:
                category_key = 'cosmetics'
            else:
                continue

            next_update_times[category_key] = update_seconds

            ul = section.find('ul')
            if not ul:
                continue

            for li in ul.find_all('li'):
                item_text = li.get_text().strip()
                if not item_text:
                    continue

                if ' x' in item_text:
                    parts = item_text.rsplit(' x', 1)
                    name = parts[0].strip()
                    try:
                        stock = int(parts[1].strip())
                    except (ValueError, IndexError):
                        stock = 0
                else:
                    name = item_text.strip()
                    stock = 1

                if name:
                    new_data[category_key].append({
                        'name': name,
                        'stock': stock,
                        'price': 0
                    })

        for category, items in new_data.items():
            save_to_db(category, items, last_updated)

        if next_update_times:
            min_seconds = min(next_update_times.values())
            try:
                scheduler.remove_job('stock_scraper')
            except:
                pass
            scheduler.add_job(
                scrape_stock,
                'date',
                run_date=datetime.now() + timedelta(seconds=min_seconds),
                id='stock_scraper'
            )
        else:
            try:
                scheduler.remove_job('stock_scraper')
            except:
                pass
            scheduler.add_job(
                scrape_stock,
                'date',
                run_date=datetime.now() + timedelta(minutes=5),
                id='stock_scraper'
            )
    except Exception as e:
        logger.error(f"Erro no scraping: {e}")
        try:
            scheduler.remove_job('stock_scraper')
        except:
            pass
        scheduler.add_job(
            scrape_stock,
            'date',
            run_date=datetime.now() + timedelta(minutes=2),
            id='stock_scraper'
        )

scheduler = BackgroundScheduler()
scheduler.start()

init_db()
scrape_stock()

@app.route('/')
def home():
    return jsonify({
        'message': 'API de Estoque Grow a Garden',
        'endpoints': {
            '/api/grow-a-garden/stock': 'GET - Obter dados de estoque',
            '/api/grow-a-garden/stock?category=CATEGORIA': 'GET - Obter dados de uma categoria específica',
            '/api/grow-a-garden/stock/refresh': 'GET - Forçar atualização dos dados'
        },
        'categorias_disponíveis': ['seeds', 'gear', 'egg_shop', 'honey', 'cosmetics']
    })

@app.route('/api/grow-a-garden/stock', methods=['GET'])
def get_stock():
    category = request.args.get('category')
    if category:
        items, last_updated = load_from_db(category)
        if not items:
            return jsonify({'error': 'Categoria não encontrada ou sem dados'}), 404
        return jsonify({category: items, 'last_updated': last_updated})
    return jsonify(load_from_db())

@app.route('/api/grow-a-garden/stock/refresh', methods=['GET'])
def refresh_stock():
    scrape_stock()
    return jsonify({'message': 'Dados atualizados', 'last_updated': load_from_db()['last_updated']})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)

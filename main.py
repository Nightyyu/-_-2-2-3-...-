from flask import Flask, jsonify, request
import sqlite3
import os
import logging
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from fake_useragent import UserAgent
import undetected_chromedriver as uc
from selenium.webdriver.chrome.options import Options
import re

app = Flask(__name__)

# Configuração de logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Caminho do banco de dados para Render
DB_PATH = os.path.join('/opt/render/project/src/data', 'stock_data.db')  # Use persistent disk

def init_db():
    """Inicializa o banco de dados SQLite."""
    try:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)  # Criar diretório se não existir
        with sqlite3.connect(DB_PATH) as conn:
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
            logger.info(f"Banco de dados inicializado em {DB_PATH}")
    except sqlite3.Error as e:
        logger.error(f"Erro ao inicializar o banco de dados: {str(e)}")
        raise

def save_to_db(category, items, last_updated):
    """Salva dados no banco de dados."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM stock WHERE category = ?', (category,))
            for item in items:
                cursor.execute('''
                    INSERT INTO stock (category, name, stock, price, last_updated)
                    VALUES (?, ?, ?, ?, ?)
                ''', (category, item['name'], item['stock'], item.get('price', 0), last_updated))
            conn.commit()
            logger.info(f"Dados salvos para a categoria {category}")
    except sqlite3.Error as e:
        logger.error(f"Erro ao salvar dados no banco: {str(e)}")
        raise

def load_from_db(category=None):
    """Carrega dados do banco de dados."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='stock'")
            if not cursor.fetchone():
                logger.error("Tabela 'stock' não encontrada")
                return {'error': 'Tabela stock não encontrada'}, None

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
    except sqlite3.Error as e:
        logger.error(f"Erro ao carregar dados do banco: {str(e)}")
        return {'error': f'Erro no banco de dados: {str(e)}'}, None

def parse_update_time(time_text):
    """Converte texto como '03m 56s' ou '01h 13m 56s' em segundos."""
    time_text = time_text.lower().strip()
    pattern = r'(?:(\d+)h\s*)?(?:(\d+)m\s*)?(?:(\d+)s)?'
    match = re.search(pattern, time_text)
    if not match:
        return 300
    hours = int(match.group(1)) if match.group(1) else 0
    minutes = int(match.group(2)) if match.group(2) else 0
    seconds = int(match.group(3)) if match.group(3) else 0
    total_seconds = hours * 3600 + minutes * 60 + seconds
    return max(total_seconds, 30)

def scrape_stock():
    """Raspa os dados de estoque do site usando headless browser."""
    url = 'https://vulcanvalues.com/grow-a-garden/stock'
    last_updated = datetime.now().isoformat()
    next_update_times = {}
    ua = UserAgent()
    user_agent = ua.random
    logger.info(f"User-Agent: {user_agent}")

    chrome_options = Options()
    chrome_options.add_argument('--headless=new')  # Novo modo headless para melhor compatibilidade
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument(f'--user-agent={user_agent}')
    chrome_options.add_argument('--disable-gpu')  # Desativar GPU para Render

    try:
        # Explicitamente definir o caminho do Chrome binary
        chrome_options.binary_location = '/usr/bin/google-chrome'  # Caminho padrão no Render
        driver = uc.Chrome(options=chrome_options, version_main=129)  # Especificar versão do Chrome
        driver.get(url)
        logger.info(f"Acessando URL: {url}")
        driver.implicitly_wait(10)

        if "checking your browser" in driver.page_source.lower():
            logger.warning("Cloudflare challenge detectado, aguardando...")
            driver.implicitly_wait(15)

        soup = BeautifulSoup(driver.page_source, 'html.parser')
        driver.quit()

        new_data = {'seeds': [], 'gear': [], 'egg_shop': [], 'honey': [], 'cosmetics': []}
        stock_grid = soup.find('div', class_='grid grid-cols-1 md:grid-cols-3 gap-6 px-6 text-left max-w-screen-lg mx-auto')
        if not stock_grid:
            logger.error("Seção de estoque não encontrada")
            return

        sections_found = 0
        for section in stock_grid.find_all('div'):
            h2 = section.find('h2')
            if not h2:
                continue
            sections_found += 1
            category = h2.text.strip().lower()
            logger.info(f"Processando categoria: {category}")

            update_time_text = ""
            update_paragraph = section.find('p', string=re.compile(r'UPDATES IN:', re.IGNORECASE))
            if update_paragraph:
                update_time_text = update_paragraph.get_text()
            else:
                for p in section.find_all(['p', 'div', 'span']):
                    if p.get_text() and 'updates in:' in p.get_text().lower():
                        update_time_text = p.get_text()
                        break

            if update_time_text:
                time_match = re.search(r'updates in:\s*(.+)', update_time_text.lower())
                update_seconds = parse_update_time(time_match.group(1).strip()) if time_match else 300
                logger.info(f"Categoria {category}: próxima atualização em {update_seconds}s")
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
                logger.warning(f"Lista não encontrada para categoria: {category}")
                continue

            items_found = 0
            for li in ul.find_all('li'):
                item_text = li.get_text().strip()
                if not item_text:
                    continue
                if ' x' in item_text:
                    parts = item_text.rsplit(' x', 1)
                    name = parts[0].strip()
                    stock = int(parts[1].strip()) if parts[1].strip().isdigit() else 0
                else:
                    name = item_text.strip()
                    stock = 1
                if name:
                    new_data[category_key].append({'name': name, 'stock': stock, 'price': 0})
                    items_found += 1

            logger.info(f"Categoria {category_key}: {items_found} itens encontrados")

        for category, items in new_data.items():
            save_to_db(category, items, last_updated)

        if next_update_times:
            min_seconds = min(next_update_times.values())
            min_category = min(next_update_times, key=next_update_times.get)
            logger.info(f"Próxima atualização em {min_seconds}s (categoria: {min_category})")
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
            logger.warning("Usando 5 minutos padrão")
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
        logger.error(f"Erro ao raspar: {str(e)}")
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
    finally:
        if 'driver' in locals():
            driver.quit()

scheduler = BackgroundScheduler()
scheduler.start()

try:
    init_db()
    scrape_stock()
except Exception as e:
    logger.error(f"Erro na inicialização: {str(e)}")

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
        if isinstance(items, dict) and 'error' in items:
            return jsonify(items), 500
        if not items:
            return jsonify({'error': 'Categoria não encontrada ou sem dados'}), 404
        return jsonify({category: items, 'last_updated': last_updated})
    data = load_from_db()
    if isinstance(data, dict) and 'error' in data:
        return jsonify(data), 500
    return jsonify(data)

@app.route('/api/grow-a-garden/stock/refresh', methods=['GET'])
def refresh_stock():
    scrape_stock()
    data = load_from_db()
    if isinstance(data, dict) and 'error' in data:
        return jsonify(data), 500
    return jsonify({'message': 'Dados atualizados', 'last_updated': data['last_updated']})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)

import os
import logging
import random
import sqlite3
import requests
from datetime import datetime, timedelta
from functools import lru_cache
from collections import defaultdict
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, InputMediaPhoto
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
    JobQueue
)

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuration class
class Config:
    TMDB_API_KEY = os.getenv('TMDB_API_KEY')
    BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
    DB_FILE = os.getenv('DB_FILE', 'wiseflix.db')
    TMDB_BASE_URL = 'https://api.themoviedb.org/3'
    POSTER_BASE_URL = 'https://image.tmdb.org/t/p/original'
    DISABLE_RATE_LIMITER = os.getenv('DISABLE_RATE_LIMITER', '0') == '1'  # New rate limiter toggle
    
    @classmethod
    def validate(cls):
        if not cls.TMDB_API_KEY:
            raise ValueError("TMDB_API_KEY environment variable is missing")
        if not cls.BOT_TOKEN:
            raise ValueError("TELEGRAM_BOT_TOKEN environment variable is missing")

# Validate config
Config.validate()

# Genre mappings
GENRES = {
    'movie': {
        28: 'Action',
        12: 'Adventure',
        16: 'Animation',
        35: 'Comedy',
        80: 'Crime',
        99: 'Documentary',
        18: 'Drama',
        10751: 'Family',
        14: 'Fantasy',
        36: 'History',
        27: 'Horror',
        10402: 'Music',
        9648: 'Mystery',
        10749: 'Romance',
        878: 'Science Fiction',
        10770: 'TV Movie',
        53: 'Thriller',
        10752: 'War',
        37: 'Western'
    },
    'tv': {
        10759: 'Action & Adventure',
        16: 'Animation',
        35: 'Comedy',
        80: 'Crime',
        99: 'Documentary',
        18: 'Drama',
        10751: 'Family',
        10762: 'Kids',
        9648: 'Mystery',
        10763: 'News',
        10764: 'Reality',
        10765: 'Sci-Fi & Fantasy',
        10766: 'Soap',
        10767: 'Talk',
        10768: 'War & Politics',
        37: 'Western'
    }
}

# Rare genres that need special handling
RARE_GENRES = {99, 10770, 10763, 10764, 10767}  # Documentaries, TV Movies, News, Reality, Talk

# Enhanced recommendation configuration
GENRE_ADJUSTMENTS = {
    99: {'min_rating': 5.0, 'min_votes': 50},    # Documentaries
    16: {'min_rating': 6.0, 'min_votes': 100},   # Animation
    10770: {'min_rating': 4.5, 'min_votes': 50}, # TV Movies
    10763: {'min_rating': 4.0, 'min_votes': 30}, # News
    10764: {'min_rating': 4.0, 'min_votes': 30}, # Reality
    10767: {'min_rating': 4.0, 'min_votes': 30}  # Talk
}

# ========== DATABASE HANDLER ==========
class Database:
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(Database, cls).__new__(cls)
            cls._instance.conn = sqlite3.connect(Config.DB_FILE)
            cls._instance.create_tables()
        return cls._instance
    
    def create_tables(self):
        cursor = self.conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS watchlists (
                user_id TEXT NOT NULL,
                content_type TEXT NOT NULL,
                item_id TEXT NOT NULL,
                title TEXT NOT NULL,
                poster_path TEXT,
                date_added TEXT NOT NULL,
                PRIMARY KEY (user_id, content_type, item_id)
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS favorites (
                user_id TEXT NOT NULL,
                content_type TEXT NOT NULL,
                item_id TEXT NOT NULL,
                title TEXT NOT NULL,
                poster_path TEXT,
                date_added TEXT NOT NULL,
                PRIMARY KEY (user_id, content_type, item_id)
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS notifications (
                user_id TEXT PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 0,
                frequency TEXT NOT NULL DEFAULT 'weekly',
                content_type TEXT NOT NULL DEFAULT 'both'
            )
        ''')
        
        # Create indexes for performance
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_watchlists_user ON watchlists(user_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_favorites_user ON favorites(user_id)')
        self.conn.commit()
    
    def get_watchlist(self, user_id, offset=0, limit=None):
        cursor = self.conn.cursor()
        query = "SELECT * FROM watchlists WHERE user_id = ? ORDER BY date_added DESC"
        params = [user_id]
        
        if limit is not None:
            query += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        
        cursor.execute(query, tuple(params))
        return cursor.fetchall()
    
    def get_watchlist_count(self, user_id):
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM watchlists WHERE user_id = ?", (user_id,))
        return cursor.fetchone()[0]
    
    def add_to_watchlist(self, user_id, content_type, item_id, title, poster_path):
        cursor = self.conn.cursor()
        try:
            cursor.execute('''
                INSERT INTO watchlists (user_id, content_type, item_id, title, poster_path, date_added)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (user_id, content_type, item_id, title, poster_path, datetime.now().isoformat()))
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
    
    def remove_from_watchlist(self, user_id, content_type, item_id):
        cursor = self.conn.cursor()
        cursor.execute('''
            DELETE FROM watchlists 
            WHERE user_id = ? AND content_type = ? AND item_id = ?
        ''', (user_id, content_type, item_id))
        self.conn.commit()
        return cursor.rowcount > 0
    
    def get_favorites(self, user_id, offset=0, limit=None):
        cursor = self.conn.cursor()
        query = "SELECT * FROM favorites WHERE user_id = ? ORDER BY date_added DESC"
        params = [user_id]
        
        if limit is not None:
            query += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        
        cursor.execute(query, tuple(params))
        return cursor.fetchall()
    
    def get_favorites_count(self, user_id):
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM favorites WHERE user_id = ?", (user_id,))
        return cursor.fetchone()[0]
    
    def add_to_favorites(self, user_id, content_type, item_id, title, poster_path):
        cursor = self.conn.cursor()
        try:
            cursor.execute('''
                INSERT INTO favorites (user_id, content_type, item_id, title, poster_path, date_added)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (user_id, content_type, item_id, title, poster_path, datetime.now().isoformat()))
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
    
    def remove_from_favorites(self, user_id, content_type, item_id):
        cursor = self.conn.cursor()
        cursor.execute('''
            DELETE FROM favorites 
            WHERE user_id = ? AND content_type = ? AND item_id = ?
        ''', (user_id, content_type, item_id))
        self.conn.commit()
        return cursor.rowcount > 0
    
    def get_notification_settings(self, user_id):
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM notifications WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        if row:
            return {
                'user_id': row[0],
                'enabled': bool(row[1]),
                'frequency': row[2],
                'content_type': row[3]
            }
        return None
    
    def update_notification_settings(self, user_id, enabled=None, frequency=None, content_type=None):
        cursor = self.conn.cursor()
        settings = self.get_notification_settings(user_id) or {
            'enabled': False,
            'frequency': 'weekly',
            'content_type': 'both'
        }
        
        if enabled is not None:
            settings['enabled'] = enabled
        if frequency is not None:
            settings['frequency'] = frequency
        if content_type is not None:
            settings['content_type'] = content_type
        
        cursor.execute('''
            INSERT OR REPLACE INTO notifications (user_id, enabled, frequency, content_type)
            VALUES (?, ?, ?, ?)
        ''', (user_id, int(settings['enabled']), settings['frequency'], settings['content_type']))
        self.conn.commit()
        return settings

# Initialize database
db = Database()

# ========== ERROR HANDLER ==========
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log errors and notify user."""
    logger.error("Exception while handling an update:", exc_info=context.error)
    
    if update and update.effective_message:
        await update.effective_message.reply_text(
            "⚠️ An unexpected error occurred. Please try again later."
        )

# ========== RATE LIMITER ==========
class RateLimiter:
    def __init__(self, max_requests=15, per_seconds=60):
        self.user_activity = defaultdict(list)
        self.max_requests = max_requests
        self.per_seconds = per_seconds
    
    def check_rate_limit(self, user_id):
        now = datetime.now()
        # Remove old timestamps
        self.user_activity[user_id] = [
            t for t in self.user_activity[user_id] 
            if (now - t).total_seconds() < self.per_seconds
        ]
        
        if len(self.user_activity[user_id]) >= self.max_requests:
            return False
            
        self.user_activity[user_id].append(now)
        return True

# Initialize rate limiter
rate_limiter = RateLimiter()

# ========== BUTTON HANDLER ==========
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle all button presses with rate limiting."""
    user_id = str(update.effective_user.id)
    query = update.callback_query
    
    # Skip rate limiter if disabled in config
    if not Config.DISABLE_RATE_LIMITER:
        # Check rate limit
        if not rate_limiter.check_rate_limit(user_id):
            await query.answer("⚠️ Too many requests. Please wait a minute.", show_alert=True)
            return
    
    await query.answer()
    
    data = query.data
    
    try:
        # Validate content type when present
        if data.startswith(('random:', 'genre:', 'details:', 'add_watchlist:', 'remove_watchlist:', 
                          'add_favorite:', 'remove_favorite:')):
            parts = data.split(':')
            if len(parts) > 1 and parts[1] not in ['movie', 'tv']:
                logger.warning(f"Invalid content type: {parts[1]}")
                await query.answer("Invalid content type")
                return
                
        if data == 'main_menu':
            await main_menu(update, context)
        elif data == 'browse_genres':
            await genres(update, context)
        elif data == 'trending_menu':
            await trending(update, context)
        elif data.startswith('random:'):
            content_type = data.split(':')[1]
            await get_random_content(update, context, content_type)
        elif data.startswith('random_prev:'):
            index = int(data.split(':')[1])
            await display_random_content(update, context, index)
        elif data.startswith('random_next:'):
            index = int(data.split(':')[1])
            await display_random_content(update, context, index)
        elif data.startswith('random_back:'):
            index = int(data.split(':')[1])
            await display_random_content(update, context, index)
        elif data.startswith('genre_type:'):
            content_type = data.split(':')[1]
            await show_genre_selection(query, content_type)
        elif data.startswith('genre:'):
            parts = data.split(':')
            content_type = parts[1]
            genre_id = int(parts[2])
            await get_random_content(update, context, content_type, genre_id)
        elif data.startswith('details:'):
            parts = data.split(':')
            content_type = parts[1]
            item_id = parts[2]
            source = parts[3] if len(parts) > 3 else None
            source_page = int(parts[4]) if len(parts) > 4 else None
            await show_details(update, context, content_type, item_id, source, source_page)
        elif data.startswith('add_watchlist:'):
            parts = data.split(':')
            content_type = parts[1]
            item_id = parts[2]
            await manage_watchlist(update, context, 'add', content_type, item_id)
        elif data.startswith('remove_watchlist:'):
            parts = data.split(':')
            content_type = parts[1]
            item_id = parts[2]
            await manage_watchlist(update, context, 'remove', content_type, item_id)
        elif data.startswith('add_favorite:'):
            parts = data.split(':')
            content_type = parts[1]
            item_id = parts[2]
            await manage_favorites(update, context, 'add', content_type, item_id)
        elif data.startswith('remove_favorite:'):
            parts = data.split(':')
            content_type = parts[1]
            item_id = parts[2]
            await manage_favorites(update, context, 'remove', content_type, item_id)
        elif data.startswith('my_watchlist:'):
            page = int(data.split(':')[1])
            await show_watchlist(update, context, page)
        elif data.startswith('my_favorites:'):
            page = int(data.split(':')[1])
            await show_favorites(update, context, page)
        elif data == 'notification_settings':
            await notification_settings(update, context)
        elif data == 'toggle_notifications':
            await toggle_notifications(update, context)
        elif data == 'change_frequency':
            await change_frequency(update, context)
        elif data.startswith('set_frequency:'):
            frequency = data.split(':')[1]
            await set_frequency(update, context, frequency)
        elif data == 'change_content_type':
            await change_content_type(update, context)
        elif data.startswith('set_content_type:'):
            content_type = data.split(':')[1]
            await set_content_type(update, context, content_type)
        elif data == 'remove_menu':
            await remove_items_menu(update, context)
        elif data.startswith('remove_menu:'):
            list_type = data.split(':')[1]
            if list_type == "back":
                await remove_items_menu(update, context)
            else:
                await show_removable_items(update, context, list_type)
        elif data.startswith('confirm_remove:'):
            _, list_type, content_type, item_id = data.split(':')
            await confirm_removal(update, context, list_type, content_type, item_id)
        elif data.startswith('execute_remove:'):
            _, list_type, content_type, item_id = data.split(':')
            await execute_removal(update, context, list_type, content_type, item_id)
        elif data.startswith('trending:'):
            content_type = data.split(':')[1]
            await handle_trending(update, context, content_type)
        elif data == 'noop':
            await query.answer()
    except Exception as e:
        logger.error(f"Error handling button press: {e}")
        await query.edit_message_text("⚠️ Something went wrong. Please try again.")

# ========== ENHANCED RECOMMENDATION ENGINE ==========
@lru_cache(maxsize=32)
def get_tmdb_genres(content_type: str):
    """Cache genre list from TMDb"""
    url = f"{Config.TMDB_BASE_URL}/genre/{content_type}/list"
    params = {'api_key': Config.TMDB_API_KEY}
    response = requests.get(url, params=params)
    if response.status_code == 200:
        return response.json().get('genres', [])
    return []

def get_quality_content(content_type: str, genre_id=None):
    """Get high-quality content with smart filters and improved randomness"""
    # Weighted randomization for better results
    sort_options = [
        ('vote_average.desc', 0.6),  # 60% chance
        ('popularity.desc', 0.3),     # 30% chance
        ('primary_release_date.desc', 0.1)  # 10% chance
    ]
    weights = [w for _, w in sort_options]
    chosen_sort = random.choices([opt[0] for opt in sort_options], weights=weights)[0]
    
    base_params = {
        'api_key': Config.TMDB_API_KEY,
        'sort_by': chosen_sort,
        'page': random.randint(1, 10)  # Use first 10 pages for better quality
    }

    # Different filters for movies vs TV
    if content_type == 'movie':
        filters = {
            'vote_count.gte': 100,
            'vote_average.gte': 6.0,
            'with_original_language': 'en',
            'primary_release_date.gte': '2000-01-01'
        }
    else:  # TV shows
        filters = {
            'vote_count.gte': 50,  # Lower threshold for TV
            'vote_average.gte': 6.0,
            'with_original_language': 'en',
            'first_air_date.gte': '2010-01-01'
        }
    
    # Apply genre-specific adjustments
    if genre_id and genre_id in GENRE_ADJUSTMENTS:
        filters.update(GENRE_ADJUSTMENTS[genre_id])
    
    # Add genre filter if specified
    if genre_id:
        filters['with_genres'] = genre_id
        url = f"{Config.TMDB_BASE_URL}/discover/{content_type}"
    else:
        url = f"{Config.TMDB_BASE_URL}/{content_type}/top_rated"

    # Add region parameter for better localization
    base_params['region'] = 'US'
    
    response = requests.get(url, params={**base_params, **filters})
    
    if response.status_code == 200:
        results = response.json().get('results', [])
        
        # Dynamic fallback based on genre rarity
        min_results = 3 if genre_id in RARE_GENRES else 8
        if len(results) < min_results:
            fallback_params = {**filters}
            fallback_params.update({
                'vote_count.gte': max(30, filters.get('vote_count.gte', 0) - 20),
                'vote_average.gte': max(5.0, filters.get('vote_average.gte', 0) - 0.5)
            })
            
            response = requests.get(url, params={**base_params, **fallback_params})
            if response.status_code == 200:
                return [item for item in response.json().get('results', []) 
                        if not item.get('adult', False)]
            else:
                logger.error(f"TMDb API fallback failed: {response.status_code}")
                return []
        
        # Filter out adult content
        results = [item for item in results if not item.get('adult', False)]
        return results
    else:
        logger.error(f"TMDb API failed: {response.status_code}")
        return []

async def show_genre_selection(query, content_type):
    """Show genre selection buttons with improved layout"""
    genre_list = GENRES.get(content_type, {})
    
    # Create two columns of genre buttons
    keyboard = []
    genres_items = list(genre_list.items())
    for i in range(0, len(genres_items), 2):
        row = []
        if i < len(genres_items):
            genre_id, genre_name = genres_items[i]
            row.append(InlineKeyboardButton(
                genre_name, 
                callback_data=f"genre:{content_type}:{genre_id}"
            ))
        if i+1 < len(genres_items):
            genre_id, genre_name = genres_items[i+1]
            row.append(InlineKeyboardButton(
                genre_name, 
                callback_data=f"genre:{content_type}:{genre_id}"
            ))
        if row:
            keyboard.append(row)
    
    # Add navigation buttons
    keyboard.append([
        InlineKeyboardButton("⬅️ Back", callback_data="browse_genres"),
        InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")
    ])
    
    await query.edit_message_text(
        f"Select a {content_type} genre:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def get_random_content(update: Update, context: ContextTypes.DEFAULT_TYPE, content_type: str, genre_id: int = None):
    """Get random content with improved error handling"""
    results = get_quality_content(content_type, genre_id)
    
    if results:
        # Shuffle and limit to 20 items for better performance
        random.shuffle(results)
        results = results[:20]
        
        context.user_data['random_session'] = {
            'items': results,
            'content_type': content_type,
            'genre_id': genre_id,
            'source': 'genre' if genre_id else 'random',
            'current_index': 0,
            'last_refresh': datetime.now().isoformat()
        }
        await display_random_content(update, context, 0)
    else:
        message = f"No {content_type}s found"
        if genre_id:
            genre_name = GENRES.get(content_type, {}).get(genre_id, "this genre")
            message = f"No {content_type}s found in {genre_name}. Try another genre!"
        
        keyboard = [
            [InlineKeyboardButton("🔍 Browse Genres", callback_data="browse_genres")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
        ]
        
        if update.callback_query:
            await update.callback_query.edit_message_text(
                message,
                reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await update.message.reply_text(
                message,
                reply_markup=InlineKeyboardMarkup(keyboard))

# ========== DISPLAY RANDOM CONTENT ==========
async def display_random_content(update: Update, context: ContextTypes.DEFAULT_TYPE, index: int):
    """Display random content with navigation controls."""
    session = context.user_data.get('random_session', {})
    items = session.get('items', [])
    last_refresh = session.get('last_refresh')
    
    # Refresh content if session is old or empty
    if not items or (last_refresh and (datetime.now() - datetime.fromisoformat(last_refresh)) > timedelta(minutes=10)):
        content_type = session.get('content_type', 'movie')
        genre_id = session.get('genre_id')
        await get_random_content(update, context, content_type, genre_id)
        return
    
    # Handle negative index
    if index < 0:
        index = len(items) - 1
    else:
        index = index % len(items)
        
    item = items[index]
    content_type = session['content_type']
    context.user_data['random_session']['current_index'] = index
    
    buttons = []
    buttons.append([
        InlineKeyboardButton("⬅️ Previous", callback_data=f"random_prev:{index-1}"),
        InlineKeyboardButton(f"{index+1}/{len(items)}", callback_data="noop"),
        InlineKeyboardButton("Next ➡️", callback_data=f"random_next:{index+1}")
    ])
    
    buttons.append([
        InlineKeyboardButton("🎬 More Info", callback_data=f"details:{content_type}:{item['id']}"),
        InlineKeyboardButton("🔀 New Random", callback_data=f"random:{content_type}")
    ])
    
    user_id = str(update.effective_user.id)
    item_id = str(item['id'])
    
    # Check if in watchlist/favorites
    watchlist_items = db.get_watchlist(user_id)
    in_watchlist = any(
        row[1] == content_type and row[2] == item_id 
        for row in watchlist_items
    ) if watchlist_items else False
    
    favorites_items = db.get_favorites(user_id)
    in_favorites = any(
        row[1] == content_type and row[2] == item_id 
        for row in favorites_items
    ) if favorites_items else False
    
    watchlist_button = InlineKeyboardButton(
        "✅ In Watchlist" if in_watchlist else "➕ Add to Watchlist",
        callback_data=f"remove_watchlist:{content_type}:{item_id}" if in_watchlist else f"add_watchlist:{content_type}:{item_id}"
    )
    favorite_button = InlineKeyboardButton(
        "❤️ In Favorites" if in_favorites else "⭐ Add to Favorites",
        callback_data=f"remove_favorite:{content_type}:{item_id}" if in_favorites else f"add_favorite:{content_type}:{item_id}"
    )
    buttons.append([watchlist_button, favorite_button])
    
    reply_markup = InlineKeyboardMarkup(buttons)
    poster_path = item.get('poster_path')
    title = item.get('title' if content_type == 'movie' else 'name', 'Unknown')
    release_date = item.get('release_date' if content_type == 'movie' else 'first_air_date', 'Unknown')
    year = release_date[:4] if release_date and release_date != 'Unknown' else 'Unknown'
    caption = f"<b>{title}</b> ({year})"
    
    # Handle missing posters
    if poster_path:
        photo_url = f"{Config.POSTER_BASE_URL}{poster_path}"
        if update.callback_query:
            try:
                await update.callback_query.edit_message_media(
                    media=InputMediaPhoto(photo_url, caption=caption, parse_mode='HTML'),
                    reply_markup=reply_markup)
            except Exception as e:
                logger.error(f"Error editing media: {e}")
                await update.callback_query.message.reply_photo(
                    photo=photo_url,
                    caption=caption,
                    parse_mode='HTML',
                    reply_markup=reply_markup)
        else:
            await update.message.reply_photo(
                photo=photo_url,
                caption=caption,
                parse_mode='HTML',
                reply_markup=reply_markup)
    else:
        # Send text-only if no poster available
        if update.callback_query:
            await update.callback_query.edit_message_text(
                caption,
                parse_mode='HTML',
                reply_markup=reply_markup)
        else:
            await update.message.reply_text(
                caption,
                parse_mode='HTML',
                reply_markup=reply_markup)

# ========== DETAILS VIEW ==========
async def show_details(update: Update, context: ContextTypes.DEFAULT_TYPE, content_type: str, item_id: str, source: str = None, source_page: int = None):
    """Show detailed information about a specific item."""
    url = f"{Config.TMDB_BASE_URL}/{content_type}/{item_id}"
    params = {
        'api_key': Config.TMDB_API_KEY,
        'append_to_response': 'videos'
    }
    
    response = requests.get(url, params=params)
    if response.status_code == 200:
        item = response.json()
        title = item.get('title' if content_type == 'movie' else 'name', 'Unknown')
        overview = item.get('overview', 'No overview available.')
        release_date = item.get('release_date' if content_type == 'movie' else 'first_air_date', 'Unknown')
        vote_average = item.get('vote_average', '?')
        
        trailer_url = None
        videos = item.get('videos', {}).get('results', [])
        for video in videos:
            if video.get('type') == 'Trailer' and video.get('site') == 'YouTube':
                trailer_url = f"https://www.youtube.com/watch?v={video.get('key')}"
                break
        
        message = (
            f"🎬 <b>{title}</b> ({release_date[:4] if release_date and release_date != 'Unknown' else 'Unknown'})\n"
            f"⭐ Rating: {vote_average}/10\n\n"
            f"{overview}\n\n"
        )
        
        if trailer_url:
            message += f"🎥 <a href='{trailer_url}'>Watch Trailer</a>\n"
        
        if source == 'watchlist':
            back_button = InlineKeyboardButton("⬅️ Back", callback_data=f"my_watchlist:{source_page}")
        elif source == 'favorites':
            back_button = InlineKeyboardButton("⬅️ Back", callback_data=f"my_favorites:{source_page}")
        else:
            session = context.user_data.get('random_session', {})
            current_index = session.get('current_index', 0)
            back_button = InlineKeyboardButton("⬅️ Back", callback_data=f"random_back:{current_index}")
        
        keyboard = [
            [back_button, InlineKeyboardButton("🔀 Next Random", callback_data=f"random:{content_type}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        query = update.callback_query
        if query.message.photo:
            await query.message.reply_text(
                message,
                parse_mode='HTML',
                reply_markup=reply_markup,
                disable_web_page_preview=False)
        else:
            await query.edit_message_text(
                message,
                parse_mode='HTML',
                reply_markup=reply_markup,
                disable_web_page_preview=False)
    else:
        logger.error(f"Failed to get details for {content_type}/{item_id}: {response.status_code}")
        await update.callback_query.answer("Failed to get details. Please try again.")

# ========== WATCHLIST/FAVORITES MANAGEMENT ==========
async def manage_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE, action: str, content_type: str, item_id: str):
    """Add or remove items from watchlist."""
    user_id = str(update.effective_user.id)
    
    if action == 'add':
        url = f"{Config.TMDB_BASE_URL}/{content_type}/{item_id}"
        params = {'api_key': Config.TMDB_API_KEY}
        response = requests.get(url, params=params)
        
        if response.status_code == 200:
            item = response.json()
            success = db.add_to_watchlist(
                user_id,
                content_type,
                item_id,
                item.get('title' if content_type == 'movie' else 'name'),
                item.get('poster_path')
            )
            if success:
                await update.callback_query.answer("Added to watchlist!")
            else:
                await update.callback_query.answer("Already in watchlist")
        else:
            logger.error(f"Failed to add to watchlist: {response.status_code}")
            await update.callback_query.answer("Failed to add to watchlist")
    
    elif action == 'remove':
        success = db.remove_from_watchlist(user_id, content_type, item_id)
        if success:
            await update.callback_query.answer("Removed from watchlist!")
        else:
            await update.callback_query.answer("Item not in watchlist")
    
    session = context.user_data.get('random_session', {})
    current_index = session.get('current_index', 0)
    await display_random_content(update, context, current_index)

async def manage_favorites(update: Update, context: ContextTypes.DEFAULT_TYPE, action: str, content_type: str, item_id: str):
    """Add or remove items from favorites."""
    user_id = str(update.effective_user.id)
    
    if action == 'add':
        url = f"{Config.TMDB_BASE_URL}/{content_type}/{item_id}"
        params = {'api_key': Config.TMDB_API_KEY}
        response = requests.get(url, params=params)
        
        if response.status_code == 200:
            item = response.json()
            success = db.add_to_favorites(
                user_id,
                content_type,
                item_id,
                item.get('title' if content_type == 'movie' else 'name'),
                item.get('poster_path')
            )
            if success:
                await update.callback_query.answer("Added to favorites! ❤️")
            else:
                await update.callback_query.answer("Already in favorites")
        else:
            logger.error(f"Failed to add to favorites: {response.status_code}")
            await update.callback_query.answer("Failed to add to favorites")
    
    elif action == 'remove':
        success = db.remove_from_favorites(user_id, content_type, item_id)
        if success:
            await update.callback_query.answer("Removed from favorites")
        else:
            await update.callback_query.answer("Item not in favorites")
    
    session = context.user_data.get('random_session', {})
    current_index = session.get('current_index', 0)
    await display_random_content(update, context, current_index)

async def _show_list(update: Update, context: ContextTypes.DEFAULT_TYPE, list_type: str, page: int = 1):
    """Shared function to display watchlist or favorites."""
    user_id = str(update.effective_user.id)
    items_per_page = 5
    
    # Get item count
    if list_type == 'watchlist':
        total_count = db.get_watchlist_count(user_id)
        title = "📝 Your Watchlist"
        empty_msg = "Your watchlist is empty. Add items to watch later!"
        button_text = "View Watchlist"
        items = db.get_watchlist(user_id, offset=(page-1)*items_per_page, limit=items_per_page)
    else:
        total_count = db.get_favorites_count(user_id)
        title = "❤️ Your Favorites"
        empty_msg = "You haven't added any favorites yet. ❤️"
        button_text = "View Favorites"
        items = db.get_favorites(user_id, offset=(page-1)*items_per_page, limit=items_per_page)
    
    total_pages = (total_count + items_per_page - 1) // items_per_page
    page = max(1, min(page, total_pages))
    
    if not items:
        keyboard = [
            [InlineKeyboardButton("🔍 Browse Movies", callback_data="genre_type:movie"),
             InlineKeyboardButton("🔍 Browse TV", callback_data="genre_type:tv")]
        ]
        await update.callback_query.edit_message_text(
            empty_msg,
            reply_markup=InlineKeyboardMarkup(keyboard))
        return
    
    message = f"{title} (Page {page}/{total_pages}):\n\n"
    keyboard = []
    
    for row in items:
        content_type = row[1]
        item_id = row[2]
        title = row[3]
        keyboard.append([
            InlineKeyboardButton(
                f"{title} ({'🎬' if content_type == 'movie' else '📺'})",
                callback_data=f"details:{content_type}:{item_id}:{list_type}:{page}"
            )
        ])
    
    pagination = []
    if page > 1:
        pagination.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"my_{list_type}:{page-1}"))
    if page < total_pages:
        pagination.append(InlineKeyboardButton("Next ➡️", callback_data=f"my_{list_type}:{page+1}"))
    
    if pagination:
        keyboard.append(pagination)
    
    keyboard.append([InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")])
    
    await update.callback_query.edit_message_text(
        message,
        reply_markup=InlineKeyboardMarkup(keyboard))

async def show_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 1):
    """Show user's watchlist with pagination."""
    await _show_list(update, context, 'watchlist', page)

async def show_favorites(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 1):
    """Show user's favorites with pagination."""
    await _show_list(update, context, 'favorites', page)

async def show_removable_items(update: Update, context: ContextTypes.DEFAULT_TYPE, list_type: str) -> None:
    """List items available for removal."""
    user_id = str(update.effective_user.id)
    
    if list_type == "watchlist":
        items = db.get_watchlist(user_id)
    else:
        items = db.get_favorites(user_id)
    
    if not items:
        await update.callback_query.edit_message_text(f"Your {list_type} is empty!")
        return

    keyboard = []
    for row in items:
        content_type = row[1]
        item_id = row[2]
        title = row[3]  # Title is at index 3
        
        keyboard.append([
            InlineKeyboardButton(
                f"❌ {title} ({'🎬' if content_type == 'movie' else '📺'})",
                callback_data=f"confirm_remove:{list_type}:{content_type}:{item_id}"
            )
        ])
    
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="remove_menu:back")])
    
    await update.callback_query.edit_message_text(
        f"Select item to remove from {list_type}:",
        reply_markup=InlineKeyboardMarkup(keyboard))

async def confirm_removal(update: Update, context: ContextTypes.DEFAULT_TYPE, list_type: str, content_type: str, item_id: str) -> None:
    """Show confirmation dialog for removal."""
    user_id = str(update.effective_user.id)
    
    if list_type == "watchlist":
        items = db.get_watchlist(user_id)
        # Find matching item: [user_id, content_type, item_id, title, ...]
        item = next((row for row in items if row[1] == content_type and row[2] == item_id), None)
    else:
        items = db.get_favorites(user_id)
        item = next((row for row in items if row[1] == content_type and row[2] == item_id), None)
    
    title = item[3] if item else 'this item'  # Title is at index 3
    
    keyboard = [
        [InlineKeyboardButton("✅ Yes, remove", callback_data=f"execute_remove:{list_type}:{content_type}:{item_id}")],
        [InlineKeyboardButton("🔙 Cancel", callback_data=f"remove_menu:{list_type}")]
    ]
    
    await update.callback_query.edit_message_text(
        f"Are you sure you want to remove '{title}' from your {list_type}?",
        reply_markup=InlineKeyboardMarkup(keyboard))

async def execute_removal(update: Update, context: ContextTypes.DEFAULT_TYPE, list_type: str, content_type: str, item_id: str) -> None:
    """Execute the removal of an item."""
    user_id = str(update.effective_user.id)
    
    if list_type == "watchlist":
        success = db.remove_from_watchlist(user_id, content_type, item_id)
        message = "✅ Removed from watchlist!" if success else "Item not found in watchlist"
    else:
        success = db.remove_from_favorites(user_id, content_type, item_id)
        message = "✅ Removed from favorites!" if success else "Item not found in favorites"
    
    await update.callback_query.answer(message)
    await remove_items_menu(update, context)

async def handle_trending(update: Update, context: ContextTypes.DEFAULT_TYPE, content_type: str):
    """Fetch and display trending content"""
    url = f"{Config.TMDB_BASE_URL}/trending/{content_type}/week"
    params = {
        'api_key': Config.TMDB_API_KEY,
        'vote_average.gte': 7.0,
        'vote_count.gte': 1000
    }
    
    response = requests.get(url, params=params)
    if response.status_code == 200:
        results = response.json().get('results', [])
        if results:
            context.user_data['random_session'] = {
                'items': results[:20],  # Limit to top 20 trending
                'content_type': content_type,
                'source': 'trending',
                'current_index': 0,
                'last_refresh': datetime.now().isoformat()
            }
            await display_random_content(update, context, 0)
        else:
            await update.callback_query.answer("No trending items found!")
    else:
        logger.error(f"Failed to get trending {content_type}: {response.status_code}")
        await update.callback_query.answer("Error fetching trending content")

# ========== NOTIFICATION SYSTEM ==========
async def notification_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show notification settings menu."""
    user_id = str(update.effective_user.id)
    settings = db.get_notification_settings(user_id) or {
        'enabled': False,
        'frequency': 'weekly',
        'content_type': 'both'
    }
    
    status = "✅ Enabled" if settings['enabled'] else "❌ Disabled"
    keyboard = [
        [InlineKeyboardButton(f"Notifications: {status}", callback_data="toggle_notifications")],
        [InlineKeyboardButton(f"Frequency: {settings['frequency'].capitalize()}", callback_data="change_frequency")],
        [InlineKeyboardButton(f"Content Type: {settings['content_type'].capitalize()}", callback_data="change_content_type")],
        [InlineKeyboardButton("⬅️ Back", callback_data="main_menu")]
    ]
    
    if update.callback_query:
        await update.callback_query.edit_message_text(
            "🔔 Notification Settings:",
            reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(
            "🔔 Notification Settings:",
            reply_markup=InlineKeyboardMarkup(keyboard))

async def toggle_notifications(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Toggle notifications on/off."""
    user_id = str(update.effective_user.id)
    settings = db.get_notification_settings(user_id) or {
        'enabled': False,
        'frequency': 'weekly',
        'content_type': 'both'
    }
    
    new_state = not settings['enabled']
    db.update_notification_settings(user_id, enabled=new_state)
    
    await notification_settings(update, context)
    action = "enabled" if new_state else "disabled"
    await update.callback_query.answer(f"Notifications {action}")

async def change_frequency(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show frequency options."""
    keyboard = [
        [InlineKeyboardButton("Daily", callback_data="set_frequency:daily")],
        [InlineKeyboardButton("Weekly", callback_data="set_frequency:weekly")],
        [InlineKeyboardButton("Monthly", callback_data="set_frequency:monthly")],
        [InlineKeyboardButton("⬅️ Back", callback_data="notification_settings")]
    ]
    
    await update.callback_query.edit_message_text(
        "Select notification frequency:",
        reply_markup=InlineKeyboardMarkup(keyboard))

async def set_frequency(update: Update, context: ContextTypes.DEFAULT_TYPE, frequency: str) -> None:
    """Set notification frequency."""
    user_id = str(update.effective_user.id)
    db.update_notification_settings(user_id, frequency=frequency)
    await notification_settings(update, context)
    await update.callback_query.answer(f"Frequency set to {frequency}")

async def change_content_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show content type options."""
    keyboard = [
        [InlineKeyboardButton("Movies Only", callback_data="set_content_type:movies")],
        [InlineKeyboardButton("TV Shows Only", callback_data="set_content_type:tv")],
        [InlineKeyboardButton("Both", callback_data="set_content_type:both")],
        [InlineKeyboardButton("⬅️ Back", callback_data="notification_settings")]
    ]
    
    await update.callback_query.edit_message_text(
        "Select content type for notifications:",
        reply_markup=InlineKeyboardMarkup(keyboard))

async def set_content_type(update: Update, context: ContextTypes.DEFAULT_TYPE, content_type: str) -> None:
    """Set notification content type."""
    user_id = str(update.effective_user.id)
    db.update_notification_settings(user_id, content_type=content_type)
    await notification_settings(update, context)
    await update.callback_query.answer(f"Content type set to {content_type}")

async def send_notifications(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send scheduled notifications to users."""
    logger.info("Starting notification job...")
    cursor = context.bot_data['db'].conn.cursor()
    cursor.execute("SELECT user_id FROM notifications WHERE enabled = 1")
    users = cursor.fetchall()
    
    for user_row in users:
        user_id = user_row[0]
        settings = context.bot_data['db'].get_notification_settings(user_id)
        if not settings or not settings['enabled']:
            continue
        
        # Get random content based on user preferences
        content_types = []
        if settings['content_type'] == 'movies':
            content_types = ['movie']
        elif settings['content_type'] == 'tv':
            content_types = ['tv']
        else:
            content_types = ['movie', 'tv']
        
        content_type = random.choice(content_types)
        results = get_quality_content(content_type)
        
        if results:
            item = random.choice(results[:10])  # Pick from top 10
            title = item.get('title' if content_type == 'movie' else 'name', 'Unknown')
            poster_path = item.get('poster_path')
            caption = f"🎬 Weekly Recommendation!\n\n<b>{title}</b>"
            
            try:
                if poster_path:
                    photo_url = f"{Config.POSTER_BASE_URL}{poster_path}"
                    await context.bot.send_photo(
                        chat_id=user_id,
                        photo=photo_url,
                        caption=caption,
                        parse_mode='HTML')
                else:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=caption,
                        parse_mode='HTML')
            except Exception as e:
                logger.error(f"Error sending notification to {user_id}: {e}")
    
    logger.info(f"Sent notifications to {len(users)} users")

# ========== COMMAND HANDLERS ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send welcome message with options."""
    user = update.effective_user
    keyboard = [
        [InlineKeyboardButton("🎬 Random Movie", callback_data="random:movie"),
         InlineKeyboardButton("📺 Random TV Show", callback_data="random:tv")],
        [InlineKeyboardButton("🔍 Browse Genres", callback_data="browse_genres"),
         InlineKeyboardButton("➕ My Watchlist", callback_data="my_watchlist:1")],
        [InlineKeyboardButton("❤️ My Favorites", callback_data="my_favorites:1"),
         InlineKeyboardButton("🔔 Notifications", callback_data="notification_settings")],
        [InlineKeyboardButton("🔥 Trending Now", callback_data="trending_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"🎉 Welcome {user.first_name}!\n\nDiscover movies and TV shows:",
        reply_markup=reply_markup)

async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show main menu."""
    keyboard = [
        [InlineKeyboardButton("🎬 Random Movie", callback_data="random:movie"),
         InlineKeyboardButton("📺 Random TV Show", callback_data="random:tv")],
        [InlineKeyboardButton("🔍 Browse Genres", callback_data="browse_genres"),
         InlineKeyboardButton("➕ My Watchlist", callback_data="my_watchlist:1")],
        [InlineKeyboardButton("❤️ My Favorites", callback_data="my_favorites:1"),
         InlineKeyboardButton("🔔 Notifications", callback_data="notification_settings")],
        [InlineKeyboardButton("🔥 Trending Now", callback_data="trending_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        await update.callback_query.edit_message_text(
            "🏠 Main Menu",
            reply_markup=reply_markup)
    else:
        await update.message.reply_text(
            "🏠 Main Menu",
            reply_markup=reply_markup)

async def genres(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show genre selection menu."""
    keyboard = [
        [InlineKeyboardButton("Movies", callback_data="genre_type:movie"),
         InlineKeyboardButton("TV Shows", callback_data="genre_type:tv")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        await update.callback_query.edit_message_text(
            "Browse by genre. Select content type:",
            reply_markup=reply_markup)
    else:
        await update.message.reply_text(
            "Browse by genre. Select content type:",
            reply_markup=reply_markup)

async def watchlist_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /watchlist command"""
    user_id = str(update.effective_user.id)
    if db.get_watchlist_count(user_id) == 0:
        keyboard = [
            [InlineKeyboardButton("🔍 Browse Movies", callback_data="genre_type:movie"),
             InlineKeyboardButton("🔍 Browse TV", callback_data="genre_type:tv")]
        ]
        await update.message.reply_text(
            "Your watchlist is empty. Add items to watch later!",
            reply_markup=InlineKeyboardMarkup(keyboard))
        return
    
    await update.message.reply_text(
        "Loading your watchlist...",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("View Watchlist", callback_data="my_watchlist:1")]]))

async def favorites_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /favorites command"""
    user_id = str(update.effective_user.id)
    if db.get_favorites_count(user_id) == 0:
        keyboard = [
            [InlineKeyboardButton("🔍 Browse Movies", callback_data="genre_type:movie"),
             InlineKeyboardButton("🔍 Browse TV", callback_data="genre_type:tv")]
        ]
        await update.message.reply_text(
            "You haven't added any favorites yet. ❤️",
            reply_markup=InlineKeyboardMarkup(keyboard))
        return
    
    await update.message.reply_text(
        "Loading your favorites...",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("View Favorites", callback_data="my_favorites:1")]]))

async def random_movie(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Get a random movie."""
    await get_random_content(update, context, 'movie')

async def random_tv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Get a random TV show."""
    await get_random_content(update, context, 'tv')

async def trending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show trending high-quality content"""
    keyboard = [
        [InlineKeyboardButton("🎬 Trending Movies", callback_data="trending:movie")],
        [InlineKeyboardButton("📺 Trending TV", callback_data="trending:tv")],
        [InlineKeyboardButton("⬅️ Back", callback_data="main_menu")]
    ]
    if update.message:
        await update.message.reply_text(
            "🔥 Trending this week (high-rated and popular):",
            reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.callback_query.edit_message_text(
            "🔥 Trending this week (high-rated and popular):",
            reply_markup=InlineKeyboardMarkup(keyboard))

async def remove(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Command handler for /remove."""
    await remove_items_menu(update, context)

async def remove_items_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show menu for removing items from watchlist or favorites."""
    keyboard = [
        [InlineKeyboardButton("📝 Remove from Watchlist", callback_data="remove_menu:watchlist")],
        [InlineKeyboardButton("❤️ Remove from Favorites", callback_data="remove_menu:favorites")],
        [InlineKeyboardButton("⬅️ Back", callback_data="main_menu")]
    ]
    
    if update.message:
        await update.message.reply_text(
            "Select list to remove items from:",
            reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.callback_query.edit_message_text(
            "Select list to remove items from:",
            reply_markup=InlineKeyboardMarkup(keyboard))

# ========== MAIN FUNCTION ==========
def main() -> None:
    """Start the bot."""
    try:
        application = Application.builder().token(Config.BOT_TOKEN).build()
        logger.info("✅ Application initialized")
        
        # Store database instance in bot_data
        application.bot_data['db'] = db
        
        # Initialize job queue for notifications
        job_queue = application.job_queue
        if job_queue:
            job_queue.run_repeating(
                send_notifications,
                interval=timedelta(days=7),
                first=10
            )
            logger.info("⏰ Notification job scheduled")
        
        # Register handlers
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("watchlist", watchlist_command))
        application.add_handler(CommandHandler("favorites", favorites_command))
        application.add_handler(CommandHandler("random_movie", random_movie))
        application.add_handler(CommandHandler("random_tv", random_tv))
        application.add_handler(CommandHandler("genres", genres))
        application.add_handler(CommandHandler("remove", remove))
        application.add_handler(CommandHandler("trending", trending))
        
        application.add_handler(CallbackQueryHandler(button))
        application.add_error_handler(error_handler)
        logger.info("✅ Handlers registered")
        
        logger.info("🚀 Starting bot...")
        application.run_polling(allowed_updates=Update.ALL_TYPES)
        logger.info("🤖 Bot has now stopped")
    except Exception as e:
        logger.error(f"Failed to start bot: {e}")

if __name__ == '__main__':
    main()

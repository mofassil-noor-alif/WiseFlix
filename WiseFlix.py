import logging
import random
from datetime import datetime, timedelta
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
import requests
import json
import os
from collections import defaultdict

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# File paths for data persistence
WATCHLIST_FILE = 'user_watchlists.json'
FAVORITES_FILE = 'user_favorites.json'
HISTORY_FILE = 'user_history.json'
NOTIFICATION_FILE = 'notifications.json'

# TMDB API configuration
TMDB_API_KEY = '410e7226cfd222e217635c7836d06ac3'
TMDB_BASE_URL = 'https://api.themoviedb.org/3'
POSTER_BASE_URL = 'https://image.tmdb.org/t/p/original'

# Load or initialize data files
def load_data(file_path):
    if os.path.exists(file_path):
        with open(file_path, 'r') as f:
            return json.load(f)
    return defaultdict(dict)

def save_data(data, file_path):
    with open(file_path, 'w') as f:
        json.dump(data, f)

user_watchlists = load_data(WATCHLIST_FILE)
user_favorites = load_data(FAVORITES_FILE)
user_history = load_data(HISTORY_FILE)
notifications = load_data(NOTIFICATION_FILE)

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

# Command handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a welcome message with options."""
    user = update.effective_user
    keyboard = [
        [
            InlineKeyboardButton("🎬 Random Movie", callback_data="random:movie"),
            InlineKeyboardButton("📺 Random TV Show", callback_data="random:tv")
        ],
        [
            InlineKeyboardButton("🔍 Browse Genres", callback_data="browse_genres"),
            InlineKeyboardButton("➕ My Watchlist", callback_data="my_watchlist:1")
        ],
        [
            InlineKeyboardButton("❤️ My Favorites", callback_data="my_favorites:1"),
            InlineKeyboardButton("🔔 Notifications", callback_data="notification_settings")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"🎉 Welcome {user.first_name}!\n\n"
        "Discover movies and TV shows with these options:",
        reply_markup=reply_markup
    )

async def random_movie(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Get a random movie."""
    await get_random_content(update, context, 'movie')

async def random_tv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Get a random TV show."""
    await get_random_content(update, context, 'tv')

async def get_random_content(update: Update, context: ContextTypes.DEFAULT_TYPE, content_type: str, genre_id: int = None) -> None:
    """Get random content (movie or TV show)."""
    random_page = random.randint(1, 20)
    
    params = {
        'api_key': TMDB_API_KEY,
        'sort_by': 'popularity.desc',
        'page': random_page
    }
    
    if genre_id:
        params['with_genres'] = genre_id
        url = f"{TMDB_BASE_URL}/discover/{content_type}"
    else:
        url = f"{TMDB_BASE_URL}/{content_type}/popular"
    
    response = requests.get(url, params=params)
    if response.status_code == 200:
        results = response.json().get('results', [])
        if results:
            item = random.choice(results)
            await display_content(update, context, content_type, item)
        else:
            await update.message.reply_text(f"No {content_type}s found. Please try again.")
    else:
        await update.message.reply_text("Sorry, there was an error fetching data.")

async def display_content(update: Update, context: ContextTypes.DEFAULT_TYPE, content_type: str, item: dict, page: int = 1) -> None:
    """Display content with poster and details."""
    user_id = str(update.effective_user.id)
    title = item.get('title' if content_type == 'movie' else 'name', 'Unknown')
    item_id = str(item.get('id'))
    
    # Add to viewing history for recommendations
    if user_id not in user_history:
        user_history[user_id] = []
    
    history_entry = {
        'content_type': content_type,
        'item_id': item_id,
        'title': title,
        'timestamp': datetime.now().isoformat()
    }
    user_history[user_id].append(history_entry)
    save_data(user_history, HISTORY_FILE)
    
    # Check if item is in watchlist or favorites
    in_watchlist = item_id in user_watchlists.get(user_id, {}).get(content_type, {})
    in_favorites = item_id in user_favorites.get(user_id, {}).get(content_type, {})
    
    # Prepare buttons
    buttons = []
    
    # Main action buttons
    buttons.append([
        InlineKeyboardButton("🎬 More Info", callback_data=f"details:{content_type}:{item_id}"),
        InlineKeyboardButton("🔀 Next Random", callback_data=f"random:{content_type}")
    ])
    
    # Watchlist/Favorites buttons
    watchlist_button = InlineKeyboardButton(
        "✅ In Watchlist" if in_watchlist else "➕ Add to Watchlist",
        callback_data=f"remove_watchlist:{content_type}:{item_id}" if in_watchlist else f"add_watchlist:{content_type}:{item_id}"
    )
    favorite_button = InlineKeyboardButton(
        "❤️ In Favorites" if in_favorites else "⭐ Add to Favorites",
        callback_data=f"remove_favorite:{content_type}:{item_id}" if in_favorites else f"add_favorite:{content_type}:{item_id}"
    )
    buttons.append([watchlist_button, favorite_button])
    
    # Personalized recommendations button
    if len(user_history.get(user_id, [])) > 3:
        buttons.append([
            InlineKeyboardButton("🤖 Get Recommendations", callback_data=f"recommendations:{content_type}:1")
        ])
    
    reply_markup = InlineKeyboardMarkup(buttons)
    
    # Display content
    poster_path = item.get('poster_path')
    release_date = item.get('release_date' if content_type == 'movie' else 'first_air_date', 'Unknown')
    year = release_date[:4] if release_date else 'Unknown'
    caption = f"<b>{title}</b> ({year})"
    
    if poster_path:
        photo_url = f"{POSTER_BASE_URL}{poster_path}"
        if update.callback_query:
            try:
                await update.callback_query.edit_message_media(
                    media=InputMediaPhoto(photo_url, caption=caption, parse_mode='HTML'),
                    reply_markup=reply_markup
                )
            except Exception as e:
                logger.error(f"Error editing media: {e}")
                await update.callback_query.message.reply_photo(
                    photo=photo_url,
                    caption=caption,
                    parse_mode='HTML',
                    reply_markup=reply_markup
                )
        else:
            await update.message.reply_photo(
                photo=photo_url,
                caption=caption,
                parse_mode='HTML',
                reply_markup=reply_markup
            )
    else:
        message = caption
        if update.callback_query:
            await update.callback_query.edit_message_text(
                message,
                parse_mode='HTML',
                reply_markup=reply_markup
            )
        else:
            await update.message.reply_text(
                message,
                parse_mode='HTML',
                reply_markup=reply_markup
            )

async def show_details(update: Update, context: ContextTypes.DEFAULT_TYPE, content_type: str, item_id: str):
    """Show detailed information about a specific item."""
    url = f"{TMDB_BASE_URL}/{content_type}/{item_id}"
    params = {
        'api_key': TMDB_API_KEY,
        'append_to_response': 'videos'
    }
    
    response = requests.get(url, params=params)
    if response.status_code == 200:
        item = response.json()
        title = item.get('title' if content_type == 'movie' else 'name', 'Unknown')
        overview = item.get('overview', 'No overview available.')
        release_date = item.get('release_date' if content_type == 'movie' else 'first_air_date', 'Unknown')
        vote_average = item.get('vote_average', '?')
        poster_path = item.get('poster_path')
        
        # Get trailer
        trailer_url = None
        videos = item.get('videos', {}).get('results', [])
        for video in videos:
            if video.get('type') == 'Trailer' and video.get('site') == 'YouTube':
                trailer_url = f"https://www.youtube.com/watch?v={video.get('key')}"
                break
        
        message = (
            f"🎬 <b>{title}</b> ({release_date[:4] if release_date else 'Unknown'})\n"
            f"⭐ Rating: {vote_average}/10\n\n"
            f"{overview}\n\n"
        )
        
        if trailer_url:
            message += f"🎥 <a href='{trailer_url}'>Watch Trailer</a>\n"
        
        # Determine back action
        if 'current_genre' in context.user_data:
            back_action = f"genre:{content_type}:{context.user_data['current_genre']['genre_id']}"
        else:
            back_action = f"random:{content_type}"
        
        keyboard = [
            [
                InlineKeyboardButton("⬅️ Back", callback_data=back_action),
                InlineKeyboardButton("🔀 Next Random", callback_data=back_action)
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Check if original message was a photo
        query = update.callback_query
        if query.message.photo:
            await query.message.reply_text(
                message,
                parse_mode='HTML',
                reply_markup=reply_markup,
                disable_web_page_preview=False
            )
        else:
            await query.edit_message_text(
                message,
                parse_mode='HTML',
                reply_markup=reply_markup,
                disable_web_page_preview=False
            )

async def manage_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE, action: str, content_type: str, item_id: str):
    """Add or remove items from watchlist."""
    user_id = str(update.effective_user.id)
    
    if action == 'add':
        if user_id not in user_watchlists:
            user_watchlists[user_id] = {}
        if content_type not in user_watchlists[user_id]:
            user_watchlists[user_id][content_type] = {}
        
        # Get item details to store
        url = f"{TMDB_BASE_URL}/{content_type}/{item_id}"
        params = {'api_key': TMDB_API_KEY}
        response = requests.get(url, params=params)
        
        if response.status_code == 200:
            item = response.json()
            user_watchlists[user_id][content_type][item_id] = {
                'title': item.get('title' if content_type == 'movie' else 'name'),
                'poster_path': item.get('poster_path'),
                'date_added': datetime.now().isoformat()
            }
            save_data(user_watchlists, WATCHLIST_FILE)
            await update.callback_query.answer("Added to watchlist!")
        else:
            await update.callback_query.answer("Failed to add to watchlist")
    
    elif action == 'remove':
        if user_id in user_watchlists and content_type in user_watchlists[user_id] and item_id in user_watchlists[user_id][content_type]:
            del user_watchlists[user_id][content_type][item_id]
            save_data(user_watchlists, WATCHLIST_FILE)
            await update.callback_query.answer("Removed from watchlist!")
        else:
            await update.callback_query.answer("Item not in watchlist")
    
    # Refresh the current view
    await refresh_current_view(update, context, content_type, item_id)

async def manage_favorites(update: Update, context: ContextTypes.DEFAULT_TYPE, action: str, content_type: str, item_id: str):
    """Add or remove items from favorites."""
    user_id = str(update.effective_user.id)
    
    if action == 'add':
        if user_id not in user_favorites:
            user_favorites[user_id] = {}
        if content_type not in user_favorites[user_id]:
            user_favorites[user_id][content_type] = {}
        
        # Get item details to store
        url = f"{TMDB_BASE_URL}/{content_type}/{item_id}"
        params = {'api_key': TMDB_API_KEY}
        response = requests.get(url, params=params)
        
        if response.status_code == 200:
            item = response.json()
            user_favorites[user_id][content_type][item_id] = {
                'title': item.get('title' if content_type == 'movie' else 'name'),
                'poster_path': item.get('poster_path'),
                'date_added': datetime.now().isoformat()
            }
            save_data(user_favorites, FAVORITES_FILE)
            await update.callback_query.answer("Added to favorites! ❤️")
        else:
            await update.callback_query.answer("Failed to add to favorites")
    
    elif action == 'remove':
        if user_id in user_favorites and content_type in user_favorites[user_id] and item_id in user_favorites[user_id][content_type]:
            del user_favorites[user_id][content_type][item_id]
            save_data(user_favorites, FAVORITES_FILE)
            await update.callback_query.answer("Removed from favorites")
        else:
            await update.callback_query.answer("Item not in favorites")
    
    # Refresh the current view
    await refresh_current_view(update, context, content_type, item_id)

async def refresh_current_view(update: Update, context: ContextTypes.DEFAULT_TYPE, content_type: str, item_id: str):
    """Refresh the current item view after watchlist/favorite changes."""
    url = f"{TMDB_BASE_URL}/{content_type}/{item_id}"
    params = {'api_key': TMDB_API_KEY}
    response = requests.get(url, params=params)
    
    if response.status_code == 200:
        item = response.json()
        await display_content(update, context, content_type, item)

async def show_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 1):
    """Show user's watchlist with pagination."""
    user_id = str(update.effective_user.id)
    items_per_page = 5
    
    if user_id not in user_watchlists or not any(user_watchlists[user_id].values()):
        await update.callback_query.edit_message_text(
            "Your watchlist is empty. Add items to watch later!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔍 Browse", callback_data="browse_genres")]])
        )
        return
    
    # Flatten all watchlist items
    all_items = []
    for content_type in user_watchlists[user_id]:
        for item_id, item_data in user_watchlists[user_id][content_type].items():
            all_items.append({
                'content_type': content_type,
                'item_id': item_id,
                'title': item_data['title'],
                'poster_path': item_data.get('poster_path'),
                'date_added': item_data.get('date_added')
            })
    
    # Sort by date added (newest first)
    all_items.sort(key=lambda x: x.get('date_added', ''), reverse=True)
    
    total_pages = (len(all_items) + items_per_page - 1) // items_per_page
    page = max(1, min(page, total_pages))
    
    start_idx = (page - 1) * items_per_page
    end_idx = start_idx + items_per_page
    page_items = all_items[start_idx:end_idx]
    
    message = f"📝 Your Watchlist (Page {page}/{total_pages}):\n\n"
    keyboard = []
    
    for item in page_items:
        keyboard.append([
            InlineKeyboardButton(
                f"{item['title']} ({'🎬' if item['content_type'] == 'movie' else '📺'})",
                callback_data=f"details:{item['content_type']}:{item['item_id']}"
            )
        ])
    
    # Pagination buttons
    pagination = []
    if page > 1:
        pagination.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"my_watchlist:{page-1}"))
    if page < total_pages:
        pagination.append(InlineKeyboardButton("Next ➡️", callback_data=f"my_watchlist:{page+1}"))
    
    if pagination:
        keyboard.append(pagination)
    
    keyboard.append([InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")])
    
    await update.callback_query.edit_message_text(
        message,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def show_favorites(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 1):
    """Show user's favorites with pagination."""
    user_id = str(update.effective_user.id)
    items_per_page = 5
    
    if user_id not in user_favorites or not any(user_favorites[user_id].values()):
        await update.callback_query.edit_message_text(
            "You haven't added any favorites yet. ❤️",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔍 Browse", callback_data="browse_genres")]])
        )
        return
    
    # Flatten all favorite items
    all_items = []
    for content_type in user_favorites[user_id]:
        for item_id, item_data in user_favorites[user_id][content_type].items():
            all_items.append({
                'content_type': content_type,
                'item_id': item_id,
                'title': item_data['title'],
                'poster_path': item_data.get('poster_path'),
                'date_added': item_data.get('date_added')
            })
    
    # Sort by date added (newest first)
    all_items.sort(key=lambda x: x.get('date_added', ''), reverse=True)
    
    total_pages = (len(all_items) + items_per_page - 1) // items_per_page
    page = max(1, min(page, total_pages))
    
    start_idx = (page - 1) * items_per_page
    end_idx = start_idx + items_per_page
    page_items = all_items[start_idx:end_idx]
    
    message = f"❤️ Your Favorites (Page {page}/{total_pages}):\n\n"
    keyboard = []
    
    for item in page_items:
        keyboard.append([
            InlineKeyboardButton(
                f"{item['title']} ({'🎬' if item['content_type'] == 'movie' else '📺'})",
                callback_data=f"details:{item['content_type']}:{item['item_id']}"
            )
        ])
    
    # Pagination buttons
    pagination = []
    if page > 1:
        pagination.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"my_favorites:{page-1}"))
    if page < total_pages:
        pagination.append(InlineKeyboardButton("Next ➡️", callback_data=f"my_favorites:{page+1}"))
    
    if pagination:
        keyboard.append(pagination)
    
    keyboard.append([InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")])
    
    await update.callback_query.edit_message_text(
        message,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def get_recommendations(update: Update, context: ContextTypes.DEFAULT_TYPE, content_type: str, page: int = 1):
    """Get personalized recommendations based on user history."""
    user_id = str(update.effective_user.id)
    items_per_page = 5
    
    if user_id not in user_history or len(user_history[user_id]) < 3:
        await update.callback_query.answer("We need more history to provide recommendations!")
        return
    
    # Get most watched genres from history
    genre_counts = defaultdict(int)
    for entry in user_history[user_id]:
        if entry['content_type'] == content_type:
            item_id = entry['item_id']
            url = f"{TMDB_BASE_URL}/{content_type}/{item_id}"
            params = {'api_key': TMDB_API_KEY}
            response = requests.get(url, params=params)
            
            if response.status_code == 200:
                item = response.json()
                for genre_id in item.get('genre_ids', []):
                    genre_counts[genre_id] += 1
    
    if not genre_counts:
        await update.callback_query.answer("No genre data found for recommendations")
        return
    
    # Get top 3 genres
    top_genres = sorted(genre_counts.items(), key=lambda x: x[1], reverse=True)[:3]
    genre_ids = [str(g[0]) for g in top_genres]
    
    # Get recommendations based on top genres
    url = f"{TMDB_BASE_URL}/discover/{content_type}"
    params = {
        'api_key': TMDB_API_KEY,
        'with_genres': ",".join(genre_ids),
        'sort_by': 'popularity.desc',
        'page': page
    }
    
    response = requests.get(url, params=params)
    if response.status_code == 200:
        results = response.json()
        total_pages = min(results.get('total_pages', 1), 10)  # Limit to 10 pages max
        items = results.get('results', [])[:items_per_page]
        
        if not items:
            await update.callback_query.answer("No recommendations found")
            return
        
        message = f"🤖 Personalized Recommendations (Page {page}/{total_pages}):\n\n"
        keyboard = []
        
        for item in items:
            title = item.get('title' if content_type == 'movie' else 'name', 'Unknown')
            keyboard.append([
                InlineKeyboardButton(
                    title,
                    callback_data=f"details:{content_type}:{item['id']}"
                )
            ])
        
        # Pagination buttons
        pagination = []
        if page > 1:
            pagination.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"recommendations:{content_type}:{page-1}"))
        if page < total_pages:
            pagination.append(InlineKeyboardButton("Next ➡️", callback_data=f"recommendations:{content_type}:{page+1}"))
        
        if pagination:
            keyboard.append(pagination)
        
        keyboard.append([InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")])
        
        await update.callback_query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        await update.callback_query.answer("Failed to get recommendations")

async def notification_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show notification settings menu."""
    user_id = str(update.effective_user.id)
    
    # Check current settings
    current_settings = notifications.get(user_id, {
        'enabled': False,
        'frequency': 'weekly',  # weekly or daily
        'content_type': 'both'  # movie, tv, or both
    })
    
    message = (
        "🔔 Notification Settings:\n\n"
        f"Status: {'✅ Enabled' if current_settings['enabled'] else '❌ Disabled'}\n"
        f"Frequency: {current_settings['frequency'].capitalize()}\n"
        f"Content: {'Movies & TV' if current_settings['content_type'] == 'both' else current_settings['content_type'].capitalize()}\n\n"
        "Choose an option to change:"
    )
    
    keyboard = [
        [
            InlineKeyboardButton(
                "✅ Enable" if not current_settings['enabled'] else "❌ Disable",
                callback_data="toggle_notifications"
            )
        ],
        [
            InlineKeyboardButton("Frequency", callback_data="change_frequency"),
            InlineKeyboardButton("Content Type", callback_data="change_content_type")
        ],
        [
            InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")
        ]
    ]
    
    await update.callback_query.edit_message_text(
        message,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def toggle_notifications(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle notifications on/off."""
    user_id = str(update.effective_user.id)
    
    if user_id not in notifications:
        notifications[user_id] = {
            'enabled': True,
            'frequency': 'weekly',
            'content_type': 'both'
        }
    else:
        notifications[user_id]['enabled'] = not notifications[user_id]['enabled']
    
    save_data(notifications, NOTIFICATION_FILE)
    await notification_settings(update, context)
    
    # Schedule or remove job based on new setting
    if notifications[user_id]['enabled']:
        await schedule_notification_job(context, user_id)
    else:
        await remove_notification_job(context, user_id)

async def change_frequency(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Change notification frequency."""
    user_id = str(update.effective_user.id)
    
    keyboard = [
        [
            InlineKeyboardButton("Daily", callback_data="set_frequency:daily"),
            InlineKeyboardButton("Weekly", callback_data="set_frequency:weekly")
        ],
        [
            InlineKeyboardButton("⬅️ Back", callback_data="notification_settings")
        ]
    ]
    
    await update.callback_query.edit_message_text(
        "Select notification frequency:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def set_frequency(update: Update, context: ContextTypes.DEFAULT_TYPE, frequency: str):
    """Set notification frequency."""
    user_id = str(update.effective_user.id)
    
    if user_id not in notifications:
        notifications[user_id] = {
            'enabled': True,
            'frequency': frequency,
            'content_type': 'both'
        }
    else:
        notifications[user_id]['frequency'] = frequency
    
    save_data(notifications, NOTIFICATION_FILE)
    
    # Reschedule job with new frequency
    if notifications[user_id]['enabled']:
        await remove_notification_job(context, user_id)
        await schedule_notification_job(context, user_id)
    
    await notification_settings(update, context)

async def change_content_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Change notification content type."""
    keyboard = [
        [
            InlineKeyboardButton("Movies", callback_data="set_content_type:movie"),
            InlineKeyboardButton("TV Shows", callback_data="set_content_type:tv")
        ],
        [
            InlineKeyboardButton("Both", callback_data="set_content_type:both")
        ],
        [
            InlineKeyboardButton("⬅️ Back", callback_data="notification_settings")
        ]
    ]
    
    await update.callback_query.edit_message_text(
        "Select what type of content to receive notifications about:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def set_content_type(update: Update, context: ContextTypes.DEFAULT_TYPE, content_type: str):
    """Set notification content type."""
    user_id = str(update.effective_user.id)
    
    if user_id not in notifications:
        notifications[user_id] = {
            'enabled': True,
            'frequency': 'weekly',
            'content_type': content_type
        }
    else:
        notifications[user_id]['content_type'] = content_type
    
    save_data(notifications, NOTIFICATION_FILE)
    await notification_settings(update, context)

async def schedule_notification_job(context: ContextTypes.DEFAULT_TYPE, user_id: str):
    """Schedule notification job based on user preferences."""
    if user_id not in notifications or not notifications[user_id]['enabled']:
        return
    
    frequency = notifications[user_id]['frequency']
    job_queue = context.application.job_queue
    
    # Remove any existing jobs for this user
    await remove_notification_job(context, user_id)
    
    # Schedule new job
    if frequency == 'daily':
        job_queue.run_daily(
            send_notification,
            time=datetime.strptime("09:00", "%H:%M").time(),
            days=(0, 1, 2, 3, 4, 5, 6),
            chat_id=int(user_id),
            name=f"daily_notification_{user_id}"
        )
    else:  # weekly
        job_queue.run_daily(
            send_notification,
            time=datetime.strptime("09:00", "%H:%M").time(),
            days=(5,),  # Friday
            chat_id=int(user_id),
            name=f"weekly_notification_{user_id}"
        )

async def remove_notification_job(context: ContextTypes.DEFAULT_TYPE, user_id: str):
    """Remove notification job for user."""
    job_queue = context.application.job_queue
    jobs = job_queue.get_jobs_by_name(f"daily_notification_{user_id}")
    jobs += job_queue.get_jobs_by_name(f"weekly_notification_{user_id}")
    
    for job in jobs:
        job.schedule_removal()

async def send_notification(context: ContextTypes.DEFAULT_TYPE):
    """Send notification to user."""
    job = context.job
    user_id = str(job.chat_id)
    
    if user_id not in notifications or not notifications[user_id]['enabled']:
        return
    
    content_type = notifications[user_id]['content_type']
    
    # Get upcoming releases
    if content_type == 'both':
        types = ['movie', 'tv']
    else:
        types = [content_type]
    
    message = "🎬 New Releases You Might Like:\n\n"
    items_to_show = []
    
    for ct in types:
        url = f"{TMDB_BASE_URL}/{ct}/upcoming" if ct == 'movie' else f"{TMDB_BASE_URL}/{ct}/on_the_air"
        params = {
            'api_key': TMDB_API_KEY,
            'page': 1
        }
        
        response = requests.get(url, params=params)
        if response.status_code == 200:
            results = response.json().get('results', [])[:3]  # Get top 3
            
            for item in results:
                title = item.get('title' if ct == 'movie' else 'name', 'Unknown')
                release_date = item.get('release_date' if ct == 'movie' else 'first_air_date', 'Unknown')
                items_to_show.append({
                    'title': title,
                    'type': ct,
                    'date': release_date,
                    'id': item.get('id')
                })
    
    if not items_to_show:
        message += "No new releases found this time. Check back later!"
    else:
        for item in items_to_show:
            message += (
                f"• <b>{item['title']}</b> ({'🎬' if item['type'] == 'movie' else '📺'})\n"
                f"  Release: {item['date'] if item['date'] else 'Coming soon'}\n\n"
            )
    
    keyboard = [
        [InlineKeyboardButton("🔍 Browse New Releases", callback_data="browse_new_releases")]
    ]
    
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=message,
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        logger.error(f"Failed to send notification to {user_id}: {e}")

async def browse_new_releases(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show new releases browsing options."""
    keyboard = [
        [
            InlineKeyboardButton("🎬 New Movies", callback_data="new_releases:movie:1"),
            InlineKeyboardButton("📺 New TV Shows", callback_data="new_releases:tv:1")
        ],
        [
            InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")
        ]
    ]
    
    if update.callback_query:
        await update.callback_query.edit_message_text(
            "Browse new releases:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        await update.message.reply_text(
            "Browse new releases:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

async def show_new_releases(update: Update, context: ContextTypes.DEFAULT_TYPE, content_type: str, page: int = 1):
    """Show new releases with pagination."""
    items_per_page = 5
    url = f"{TMDB_BASE_URL}/{content_type}/upcoming" if content_type == 'movie' else f"{TMDB_BASE_URL}/{content_type}/on_the_air"
    
    params = {
        'api_key': TMDB_API_KEY,
        'page': page
    }
    
    response = requests.get(url, params=params)
    if response.status_code == 200:
        results = response.json()
        total_pages = min(results.get('total_pages', 1), 10)  # Limit to 10 pages max
        items = results.get('results', [])[:items_per_page]
        
        message = f"🎉 New {content_type.capitalize()} Releases (Page {page}/{total_pages}):\n\n"
        keyboard = []
        
        for item in items:
            title = item.get('title' if content_type == 'movie' else 'name', 'Unknown')
            release_date = item.get('release_date' if content_type == 'movie' else 'first_air_date', 'Unknown')
            
            keyboard.append([
                InlineKeyboardButton(
                    f"{title} ({release_date[:4] if release_date else 'Soon'})",
                    callback_data=f"details:{content_type}:{item['id']}"
                )
            ])
        
        # Pagination buttons
        pagination = []
        if page > 1:
            pagination.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"new_releases:{content_type}:{page-1}"))
        if page < total_pages:
            pagination.append(InlineKeyboardButton("Next ➡️", callback_data=f"new_releases:{content_type}:{page+1}"))
        
        if pagination:
            keyboard.append(pagination)
        
        keyboard.append([InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")])
        
        await update.callback_query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        await update.callback_query.answer("Failed to load new releases")

async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Return to main menu."""
    user = update.effective_user
    keyboard = [
        [
            InlineKeyboardButton("🎬 Random Movie", callback_data="random:movie"),
            InlineKeyboardButton("📺 Random TV Show", callback_data="random:tv")
        ],
        [
            InlineKeyboardButton("🔍 Browse Genres", callback_data="browse_genres"),
            InlineKeyboardButton("⭐ My Watchlist", callback_data="my_watchlist:1")
        ],
        [
            InlineKeyboardButton("❤️ My Favorites", callback_data="my_favorites:1"),
            InlineKeyboardButton("🔔 Notifications", callback_data="notification_settings")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.callback_query.edit_message_text(
        f"🏠 Main Menu\n\nWhat would you like to do, {user.first_name}?",
        reply_markup=reply_markup
    )

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle all button presses."""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    try:
        if data == 'main_menu':
            await main_menu(update, context)
        
        elif data == 'browse_genres':
            await genres(update, context)
        
        elif data.startswith('random:'):
            content_type = data.split(':')[1]
            await get_random_content(update, context, content_type)
        
        elif data.startswith('genre_type:'):
            content_type = data.split(':')[1]
            await show_genre_selection(query, content_type)
        
        elif data.startswith('genre:'):
            parts = data.split(':')
            content_type = parts[1]
            genre_id = int(parts[2])
            genre_name = GENRES[content_type][genre_id]
            
            context.user_data['current_genre'] = {
                'content_type': content_type,
                'genre_id': genre_id,
                'genre_name': genre_name
            }
            
            await get_random_content(update, context, content_type, genre_id)
        
        elif data.startswith('details:'):
            parts = data.split(':')
            content_type = parts[1]
            item_id = parts[2]
            await show_details(update, context, content_type, item_id)
        
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
        
        elif data.startswith('recommendations:'):
            parts = data.split(':')
            content_type = parts[1]
            page = int(parts[2])
            await get_recommendations(update, context, content_type, page)
        
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
        
        elif data == 'browse_new_releases':
            await browse_new_releases(update, context)
        
        elif data.startswith('new_releases:'):
            parts = data.split(':')
            content_type = parts[1]
            page = int(parts[2])
            await show_new_releases(update, context, content_type, page)
    
    except Exception as e:
        logger.error(f"Error handling button press: {e}")
        await query.edit_message_text("Sorry, something went wrong. Please try again.")

async def genres(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show genre selection menu."""
    keyboard = [
        [
            InlineKeyboardButton("Movies", callback_data="genre_type:movie"),
            InlineKeyboardButton("TV Shows", callback_data="genre_type:tv"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        await update.callback_query.edit_message_text(
            "Browse by genre. First, select the type of content:",
            reply_markup=reply_markup
        )
    else:
        await update.message.reply_text(
            "Browse by genre. First, select the type of content:",
            reply_markup=reply_markup
        )

async def show_genre_selection(query, content_type):
    """Show genre selection buttons for the specified content type."""
    genre_list = GENRES.get(content_type, {})
    
    keyboard = []
    temp_row = []
    for genre_id, genre_name in genre_list.items():
        temp_row.append(
            InlineKeyboardButton(
                genre_name,
                callback_data=f"genre:{content_type}:{genre_id}"
            )
        )
        if len(temp_row) == 2:
            keyboard.append(temp_row)
            temp_row = []
    
    if temp_row:
        keyboard.append(temp_row)
    
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="browse_genres")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(
        f"Select a {content_type} genre:",
        reply_markup=reply_markup
    )

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log errors."""
    logger.error(msg="Exception while handling an update:", exc_info=context.error)

def main() -> None:
    """Start the bot with proper error handling."""
    try:
        # Initialize application
        application = Application.builder().token("8024820278:AAEPTd9hEAT_oHjgH5aA5I59E0jPyRCyNcw").build()
        print("✅ Application initialized")  # Debug log

        # Register handlers
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("random_movie", random_movie))
        application.add_handler(CommandHandler("random_tv", random_tv))
        application.add_handler(CommandHandler("genres", genres))
        application.add_handler(CallbackQueryHandler(button))
        application.add_error_handler(error_handler)
        print("✅ Handlers registered")  # Debug log

        # Start the bot with polling
        print("🚀 Starting bot...")  # Debug log
        print("🤖 Bot is now running")  # Debug log
        application.run_polling(
            allowed_updates=Update.ALL_TYPES,
            close_loop=False,  # Important for proper shutdown
            drop_pending_updates=True
        )
        print("🤖 Bot has now stopped")  # Debug log

    except Exception as e:
        print(f"❌ Error starting bot: {e}")  # Debug log
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    print("🔧 Starting bot initialization...")  # Debug log
    main()
from flask import Flask, request, jsonify, session
import requests
import logging
from functools import lru_cache
from spellchecker import SpellChecker
from ratelimit import limits, sleep_and_retry
from flask_caching import Cache
from flask_cors import CORS
import re
import certifi

# Initialize Flask app
app = Flask(__name__)
app.secret_key = 'supersecretkey'  # Required for session handling

# Session configuration
app.config['SESSION_TYPE'] = 'filesystem'
app.config['SESSION_PERMANENT'] = False
app.config['SESSION_USE_SIGNER'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SECURE'] = False  # Set to True in production if your app is served over HTTPS
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

# Enable CORS with credentials support
CORS(app, supports_credentials=True)

# Set up caching
cache = Cache(config={'CACHE_TYPE': 'SimpleCache'})
cache.init_app(app)

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# TheMealDB API base URL
THEMEALDB_BASE_URL = 'https://www.themealdb.com/api/json/v1/1'

# Rate limit parameters (50 requests per minute)
ONE_MINUTE = 60

# Initialize spell checker
spell = SpellChecker()

# Static synonym map for common ingredient variations
SYNONYM_MAP = {
    "scallion": "green onion",
    "capsicum": "bell pepper",
    "coriander": "cilantro",
    "aubergine": "eggplant",
    "courgette": "zucchini",
    "rocket": "arugula",
}

# Fetch synonyms with caching to reduce API calls
@lru_cache(maxsize=1000)
def correct_ingredient(ingredient):
    corrected = spell.correction(ingredient)
    return SYNONYM_MAP.get(corrected, corrected)

# Fetch recipe details from TheMealDB for filtering by all ingredients
def fetch_recipe_details(meal_id):
    cache_key = f'recipe_details_{meal_id}'
    cached_data = cache.get(cache_key)
    if cached_data:
        logging.info(f"Cache hit for recipe details of meal ID: {meal_id}")
        return cached_data

    try:
        response = requests.get(
            f'{THEMEALDB_BASE_URL}/lookup.php',
            params={'i': meal_id},
            verify=certifi.where()
        )
        if response.status_code == 200:
            data = response.json().get('meals', [{}])[0]

            # Extract ingredients and measure details
            ingredients = [
                f"{data.get(f'strIngredient{i}', '').strip()} {data.get(f'strMeasure{i}', '').strip()}"
                for i in range(1, 21)
                if data.get(f'strIngredient{i}')
            ] or ['Ingredients not available']

            # Extract instructions
            instructions = data.get('strInstructions', 'Instructions not available.')

            # Calculate sustainability score based on ingredient types
            sustainability_score = calculate_sustainability_score(ingredients)

            # Create detailed recipe dictionary
            recipe_details = {
                'ingredients': ingredients,
                'instructions': instructions,
                'sustainability_score': sustainability_score,
            }

            # Cache the recipe details
            cache.set(cache_key, recipe_details)
            logging.info(f"Recipe details fetched and cached for meal ID: {meal_id}")
            return recipe_details
        else:
            logging.error(f"Failed to fetch recipe details from TheMealDB for meal ID {meal_id}. Status code: {response.status_code}")
    except Exception as e:
        logging.error(f"Error fetching recipe details for meal ID {meal_id}: {str(e)}")
    return {'ingredients': ['Ingredients not available'], 'instructions': 'Instructions not available.', 'sustainability_score': 'Unknown'}

# Calculate sustainability score based on ingredient types
def calculate_sustainability_score(ingredients):
    # A simple scoring system based on ingredient type and impact
    score = 100  # Start with a perfect score
    for ingredient in ingredients:
        if 'beef' in ingredient.lower() or 'pork' in ingredient.lower():
            score -= 20  # High impact ingredients reduce score
        elif 'vegetable' in ingredient.lower() or 'fruit' in ingredient.lower():
            score += 5  # Low impact, sustainable ingredients improve score
    return max(0, min(score, 100))  # Ensure score is between 0 and 100

# Fetch recipes based on a single ingredient with rate limiting
@sleep_and_retry
@limits(calls=50, period=ONE_MINUTE)
def fetch_recipes_by_ingredient(ingredient):
    try:
        logging.info(f"Fetching recipes for ingredient: {ingredient}")
        response = requests.get(
            f'{THEMEALDB_BASE_URL}/filter.php',
            params={'i': ingredient},
            verify=certifi.where()
        )
        if response.status_code == 200:
            data = response.json()
            if not data['meals']:
                logging.info(f"No recipes found for the ingredient: {ingredient}.")
                return []
            logging.info(f"Fetched {len(data['meals'])} recipes for ingredient: {ingredient}.")
            return data['meals']
        else:
            logging.error(f"Failed to fetch recipes from TheMealDB. Status code: {response.status_code}")
            return []
    except Exception as e:
        logging.error(f"Error fetching recipes: {str(e)}")
        return []

@app.route('/recipes', methods=['GET'])
def get_recipe():
    ingredients = request.args.get('ingredients', '')
    logging.info(f"Received ingredients: {ingredients}")
    if not ingredients:
        return jsonify({"error": "Ingredients are required."}), 400

    ingredient_list = [correct_ingredient(ingredient.strip().lower()) for ingredient in ingredients.split(',') if ingredient.strip()]
    logging.info(f"Processed ingredient list: {ingredient_list}")

    if not ingredient_list:
        return jsonify({"error": "Invalid or empty ingredients provided."}), 400

    # Clear session data if new ingredients are provided
    if session.get('last_ingredients') != ingredient_list:
        session.pop('recipes', None)
        session.pop('index', None)
        session['last_ingredients'] = ingredient_list

    if 'recipes' not in session or not session['recipes']:
        logging.info("Session does not have recipes or is empty. Fetching new recipes.")
        all_recipes = []
        for ingredient in ingredient_list:
            fetched_recipes = fetch_recipes_by_ingredient(ingredient)
            logging.info(f"Fetched {len(fetched_recipes)} recipes for ingredient: {ingredient}")
            all_recipes.extend(fetched_recipes)
        session['recipes'] = all_recipes
        session['index'] = 0
        logging.info(f"Session initialized with {len(all_recipes)} recipes.")
        logging.info(f"Session Data: {[recipe['idMeal'] for recipe in session['recipes']]}, Index: {session['index']}")

    session.setdefault('index', 0)
    session.setdefault('recipes', [])

    index = session.get('index', 0)
    recipes = session.get('recipes', [])
    logging.info(f"Session state before returning recipe: Index: {index}, Recipes Count: {len(recipes)}")
    
    if index >= len(recipes):
        session.pop('recipes', None)
        session.pop('index', None)
        return jsonify({"message": "No more recipes available."}), 404

    current_recipe = recipes[index]
    recipe_details = fetch_recipe_details(current_recipe['idMeal'])

    return jsonify({
        'id': current_recipe['idMeal'],
        'title': current_recipe.get('strMeal', 'Unknown Recipe'),
        'image': current_recipe.get('strMealThumb', 'https://via.placeholder.com/150'),
        'ingredients': recipe_details.get('ingredients', ['Ingredients not available']),
        'instructions': recipe_details.get('instructions', 'Instructions not available.'),
        'sustainability_score': recipe_details.get('sustainability_score', 'Unknown')
    })

@app.route('/feedback', methods=['POST'])
def handle_feedback():
    feedback = request.json.get('feedback')
    session_data = session.get('recipes', [])
    index = session.get('index', 0)

    # Ensure session data is valid before processing
    if not session_data or index >= len(session_data):
        return jsonify({"error": "Session data missing. Please start with new ingredients."}), 400

    if feedback == 'yes':
        # Fetch the detailed recipe information to show instructions
        selected_recipe = session_data[index]
        detailed_recipe = fetch_recipe_details(selected_recipe['idMeal'])

        # Include instructions, sustainability score, and other details
        response_data = {
            'title': selected_recipe.get('strMeal', 'Unknown Recipe'),
            'image': selected_recipe.get('strMealThumb', 'https://via.placeholder.com/150'),
            'ingredients': detailed_recipe.get('ingredients', ['Ingredients not available']),
            'instructions': detailed_recipe.get('instructions', 'No instructions available.'),
            'sustainability_score': detailed_recipe.get('sustainability_score', 'Unknown')
        }

        return jsonify({"message": "Feedback acknowledged. Displaying full recipe details.", "recipe": response_data}), 200

    elif feedback == 'no':
        # Continue to the next recipe
        index += 1
        if index < len(session_data):
            session['index'] = index
            next_recipe = session_data[index]
            next_recipe_details = fetch_recipe_details(next_recipe['idMeal'])
            return jsonify({
                "id": next_recipe.get('idMeal', 'Unknown ID'),
                "image": next_recipe.get('strMealThumb', 'https://via.placeholder.com/150'),
                "title": next_recipe.get('strMeal', 'Unknown Recipe'),
                "ingredients": next_recipe_details.get('ingredients', ['Ingredients not available']),
                "instructions": next_recipe_details.get('instructions', 'Instructions not available.'),
                "sustainability_score": next_recipe_details.get('sustainability_score', 'Unknown')
            }), 200
        else:
            return jsonify({"message": "No more recipes available."}), 404

    return jsonify({"error": "Invalid feedback received."}), 400


if __name__ == '__main__':
    app.run(debug=True, port=5001)

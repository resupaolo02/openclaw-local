"""
Nutrition Tracker Service — personal calorie & macro tracker.
Stores data in SQLite. Workspace-mounted for persistence.

Food database is backed by:
  * ~130 seeded Philippine dishes & fast-food chains (always available offline)
  * Open Food Facts  — global branded/packaged foods, no API key required
  * USDA FoodData Central — generic / raw ingredients (DEMO_KEY or env USDA_API_KEY)
"""

import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

app = FastAPI(title="Nutrition Tracker")
logger = logging.getLogger("nutrition")

WORKSPACE     = Path("/workspace")
DB_PATH       = WORKSPACE / "openclaw.db"
USDA_KEY      = os.getenv("USDA_API_KEY", "DEMO_KEY")
OFF_BASE      = "https://world.openfoodfacts.org"
USDA_BASE     = "https://api.nal.usda.gov/fdc/v1"
HTTP_TIMEOUT  = 8.0   # seconds per external request

# === DDL ======================================================================

DDL = """
CREATE TABLE IF NOT EXISTS food_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    date         TEXT    NOT NULL,
    time         TEXT    NOT NULL DEFAULT '00:00:00',
    meal_type    TEXT    NOT NULL DEFAULT 'snack',
    food_name    TEXT    NOT NULL,
    serving_size TEXT    NOT NULL DEFAULT '1 serving',
    calories     REAL    NOT NULL DEFAULT 0.0,
    protein_g    REAL    NOT NULL DEFAULT 0.0,
    carbs_g      REAL    NOT NULL DEFAULT 0.0,
    fat_g        REAL    NOT NULL DEFAULT 0.0,
    fiber_g      REAL    NOT NULL DEFAULT 0.0,
    sugar_g      REAL    NOT NULL DEFAULT 0.0,
    sodium_mg    REAL    NOT NULL DEFAULT 0.0,
    notes        TEXT    NOT NULL DEFAULT '',
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_fl_date      ON food_log(date);
CREATE INDEX IF NOT EXISTS idx_fl_meal_type ON food_log(meal_type);

CREATE TABLE IF NOT EXISTS daily_goals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    calories    REAL NOT NULL DEFAULT 2000.0,
    protein_g   REAL NOT NULL DEFAULT 150.0,
    carbs_g     REAL NOT NULL DEFAULT 200.0,
    fat_g       REAL NOT NULL DEFAULT 65.0,
    fiber_g     REAL NOT NULL DEFAULT 25.0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS food_database (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    external_id  TEXT    NOT NULL DEFAULT '',
    source       TEXT    NOT NULL DEFAULT 'custom',
    food_name    TEXT    NOT NULL,
    brand        TEXT    NOT NULL DEFAULT '',
    serving_size TEXT    NOT NULL DEFAULT '100g',
    serving_g    REAL    NOT NULL DEFAULT 100.0,
    calories     REAL    NOT NULL DEFAULT 0.0,
    protein_g    REAL    NOT NULL DEFAULT 0.0,
    carbs_g      REAL    NOT NULL DEFAULT 0.0,
    fat_g        REAL    NOT NULL DEFAULT 0.0,
    fiber_g      REAL    NOT NULL DEFAULT 0.0,
    sugar_g      REAL    NOT NULL DEFAULT 0.0,
    sodium_mg    REAL    NOT NULL DEFAULT 0.0,
    tags         TEXT    NOT NULL DEFAULT '',
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_fdb_name ON food_database(food_name);
CREATE INDEX IF NOT EXISTS idx_fdb_ext  ON food_database(external_id, source);
CREATE INDEX IF NOT EXISTS idx_fdb_src  ON food_database(source);
"""

DEFAULT_GOALS = {
    "calories":  2000.0,
    "protein_g": 150.0,
    "carbs_g":   200.0,
    "fat_g":     65.0,
    "fiber_g":   25.0,
}

# === Philippine Food Seed Data ================================================
# Sources: FNRI-DOST Philippine Food Composition Tables (2019 ed.),
# published fast-food chain nutrition guides, USDA SR Legacy cross-reference.
# All values are per the stated serving_size.

def _s(n, br, sv, sg, cal, pro, carb, fat, fib=0.0, sug=0.0, sod=0.0, tags=""):
    return dict(food_name=n, brand=br, serving_size=sv, serving_g=sg,
                calories=cal, protein_g=pro, carbs_g=carb, fat_g=fat,
                fiber_g=fib, sugar_g=sug, sodium_mg=sod, tags=tags)

PH_SEED_FOODS = [
    # --- Traditional Dishes ---
    _s("Chicken Adobo","","100g",100,230,24,3,13,0.2,1,480,"Filipino,adobo,chicken,main dish"),
    _s("Pork Adobo","","100g",100,280,22,3,20,0.2,1,520,"Filipino,adobo,pork,main dish"),
    _s("Adobong Pusit","","100g",100,150,18,5,6,0,1,560,"Filipino,adobo,squid,seafood"),
    _s("Sinigang na Baboy","","1 bowl (400ml)",400,250,22,10,13,2,2,700,"Filipino,sinigang,pork,soup"),
    _s("Sinigang na Hipon","","1 bowl (400ml)",400,180,20,10,5,2,2,650,"Filipino,sinigang,shrimp,soup"),
    _s("Sinigang na Bangus","","1 bowl (400ml)",400,200,22,10,7,2,2,630,"Filipino,sinigang,bangus,milkfish,soup"),
    _s("Kare-Kare","","1 serving (200g)",200,380,28,15,24,3,3,500,"Filipino,kare-kare,oxtail,peanut"),
    _s("Lechon Kawali","","100g",100,350,20,8,28,0,0,380,"Filipino,lechon kawali,pork,fried"),
    _s("Lechon Baboy","","100g",100,320,22,2,25,0,0,360,"Filipino,lechon,pork,roasted"),
    _s("Tinolang Manok","","1 bowl (350ml)",350,180,22,8,6,1.5,2,480,"Filipino,tinola,chicken,soup,ginger"),
    _s("Pinakbet","","100g",100,120,8,10,5,3,3,400,"Filipino,pinakbet,vegetables,bagoong"),
    _s("Paksiw na Bangus","","100g",100,160,20,3,7,0,1,450,"Filipino,paksiw,bangus,milkfish,vinegar"),
    _s("Paksiw na Lechon","","100g",100,300,20,10,20,0.5,2,480,"Filipino,paksiw,lechon,pork"),
    _s("Menudo","","100g",100,200,15,12,10,1,3,420,"Filipino,menudo,pork,tomato"),
    _s("Caldereta","","100g",100,250,18,10,15,1.5,3,460,"Filipino,caldereta,beef,tomato"),
    _s("Mechado","","100g",100,220,16,10,13,1,3,440,"Filipino,mechado,beef,tomato"),
    _s("Bistek Tagalog","","100g",100,200,22,5,10,0.5,2,500,"Filipino,bistek,beef steak,onion,soy"),
    _s("Nilaga","","1 bowl (350ml)",350,280,26,15,12,2,2,480,"Filipino,nilaga,beef,soup,boiled"),
    _s("Bulalo","","1 serving (350ml)",350,450,35,8,30,1,2,520,"Filipino,bulalo,beef,bone marrow,soup"),
    _s("Laing","","100g",100,200,8,10,15,3,2,350,"Filipino,laing,taro,coconut milk,Bicolano"),
    _s("Dinuguan","","100g",100,180,14,4,12,0.5,1,460,"Filipino,dinuguan,pork blood,stew"),
    _s("Crispy Pata","","1 serving (200g)",200,700,40,10,55,0,0,620,"Filipino,crispy pata,pork knuckle,fried"),
    _s("Inihaw na Liempo","","100g",100,280,20,3,21,0,2,400,"Filipino,liempo,pork belly,grilled,inihaw"),
    _s("Pinoy BBQ Pork (Stick)","","1 stick (80g)",80,180,14,8,10,0,5,380,"Filipino,BBQ,pork,street food"),
    _s("Inasal na Manok","","100g",100,200,25,4,10,0,2,420,"Filipino,inasal,chicken,grilled,Visayan"),
    _s("Pork Sisig","","1 serving (150g)",150,390,28,5,30,0,1,680,"Filipino,sisig,pork,sizzling,Pampanga"),
    _s("Chicken Sisig","","1 serving (150g)",150,310,26,5,20,0,1,620,"Filipino,sisig,chicken,sizzling"),
    _s("Pork Igado","","100g",100,230,18,6,15,0.5,1,500,"Filipino,igado,pork liver,Ilocano"),
    _s("Binagoongang Baboy","","100g",100,290,18,5,22,0.5,1,750,"Filipino,binagoongan,pork,bagoong,shrimp paste"),
    _s("Pochero","","1 serving (300ml)",300,350,28,20,18,3,5,560,"Filipino,pochero,beef,sausage,stew"),
    _s("Nilagang Baboy","","1 bowl (350ml)",350,300,24,14,16,2,2,460,"Filipino,nilaga,pork,soup,boiled"),
    _s("Ginataang Hipon","","100g",100,180,16,5,11,0.5,2,400,"Filipino,ginataan,shrimp,coconut milk"),
    _s("Escabeche","","100g",100,170,18,12,6,0.5,8,380,"Filipino,escabeche,fish,sweet and sour"),

    # --- Breakfast / Silog ---
    _s("Tapsilog","","1 full meal",350,650,35,65,25,1,4,780,"Filipino,silog,tapsilog,breakfast"),
    _s("Tocilog","","1 full meal",360,700,30,70,28,1,8,720,"Filipino,silog,tocilog,breakfast"),
    _s("Longsilog","","1 full meal",360,680,28,68,30,1,5,760,"Filipino,silog,longsilog,breakfast,longganisa"),
    _s("Bangsilog","","1 full meal",370,580,38,62,18,1,2,680,"Filipino,silog,bangsilog,bangus,breakfast"),
    _s("Cornsilog","","1 full meal",360,620,28,70,22,1,2,820,"Filipino,silog,cornsilog,corned beef,breakfast"),
    _s("Hotsilog","","1 full meal",340,640,26,68,28,1,3,900,"Filipino,silog,hotsilog,hotdog,breakfast"),
    _s("Sinangag","","1 cup (180g)",180,260,5,48,5,0.5,0,220,"Filipino,sinangag,garlic fried rice,breakfast"),
    _s("Champorado","","1 bowl (250ml)",250,300,7,58,5,1,15,120,"Filipino,champorado,chocolate,rice porridge,breakfast"),
    _s("Pandesal","","1 piece (50g)",50,110,3,22,1.5,0.5,2,180,"Filipino,pandesal,bread,breakfast"),
    _s("Ensaymada","","1 piece (80g)",80,280,6,38,12,0.5,10,200,"Filipino,ensaymada,bread,pastry,breakfast"),
    _s("Filipino Longganisa","","1 link (60g)",60,180,10,8,12,0,4,400,"Filipino,longganisa,sausage,breakfast"),
    _s("Tocino","","1 serving (80g)",80,220,14,20,9,0,15,380,"Filipino,tocino,pork,sweet,breakfast"),
    _s("Daing na Bangus","","1/4 fish (120g)",120,200,28,2,10,0,0,520,"Filipino,daing,bangus,milkfish,breakfast,vinegar"),
    _s("Beef Tapa","","100g",100,250,30,5,12,0,3,600,"Filipino,beef tapa,breakfast,cured"),

    # --- Rice & Noodles ---
    _s("Steamed White Rice","","1 cup cooked (186g)",186,240,4.4,53,0.4,0.6,0,0,"Filipino,rice,white rice,staple"),
    _s("Java Rice","","1 cup (180g)",180,270,5,50,6,0.5,1,200,"Filipino,java rice,yellow rice,fast food"),
    _s("Arroz Caldo","","1 bowl (350ml)",350,280,18,38,6,1,1,500,"Filipino,arroz caldo,lugaw,rice porridge,chicken,congee"),
    _s("Goto (Beef Tripe Congee)","","1 bowl (350ml)",350,300,20,38,8,0.5,1,560,"Filipino,goto,rice porridge,beef tripe,congee"),
    _s("Pancit Palabok","","1 serving (250g)",250,450,20,65,12,1,3,720,"Filipino,pancit,palabok,noodles,shrimp"),
    _s("Pancit Canton","","1 serving (200g)",200,380,18,55,10,2,3,680,"Filipino,pancit,canton,noodles,stir-fry"),
    _s("Pancit Bihon","","1 serving (200g)",200,320,15,50,7,1.5,2,620,"Filipino,pancit,bihon,rice noodles,stir-fry"),
    _s("Pancit Malabon","","1 serving (250g)",250,480,22,65,14,1,3,750,"Filipino,pancit,malabon,noodles,seafood"),
    _s("Lomi","","1 bowl (400ml)",400,380,22,45,10,1,2,700,"Filipino,lomi,noodles,soup,Batangas"),
    _s("Mami (Noodle Soup)","","1 bowl (400ml)",400,320,20,42,8,1,1,720,"Filipino,mami,noodle soup,chicken,beef"),

    # --- Seafood ---
    _s("Inihaw na Bangus","","100g",100,160,22,2,7,0,0,250,"Filipino,bangus,milkfish,grilled,inihaw"),
    _s("Tinapa (Smoked Fish)","","100g",100,200,28,0,10,0,0,600,"Filipino,tinapa,smoked fish,breakfast"),
    _s("Crispy Fried Tilapia","","100g",100,220,25,8,10,0,0,300,"Filipino,tilapia,fried,fish"),
    _s("Sugpo (Grilled Tiger Prawn)","","100g",100,95,20,1,1,0,0,180,"Filipino,sugpo,prawn,shrimp,grilled"),
    _s("Tahong (Mussels)","","100g",100,86,12,4,2,0,0,286,"Filipino,tahong,mussels,seafood"),
    _s("Halaan Clam Soup","","1 bowl (300ml)",300,120,14,5,3,0,1,520,"Filipino,halaan,clam,soup,tinola"),

    # --- Street Food ---
    _s("Kwek-Kwek","","1 piece (30g)",30,80,4,8,4,0,0,120,"Filipino,kwek-kwek,street food,quail egg"),
    _s("Fishball","","1 piece (15g)",15,30,2,4,1,0,0,80,"Filipino,fishball,street food,fish"),
    _s("Kikiam","","1 piece (25g)",25,50,3,5,2,0,0,100,"Filipino,kikiam,street food"),
    _s("Isaw Manok","","1 stick (50g)",50,90,8,5,4,0,2,220,"Filipino,isaw,chicken intestine,street food,grilled"),
    _s("Betamax (Chicken Blood Cake)","","1 piece (40g)",40,60,6,2,3,0,0,180,"Filipino,betamax,chicken blood,street food"),
    _s("Banana Cue","","1 piece (80g)",80,150,1,32,3,2,20,20,"Filipino,banana cue,banana,street food,fried"),
    _s("Camote Cue","","1 piece (70g)",70,130,1,30,2,2,16,20,"Filipino,camote cue,sweet potato,street food"),
    _s("Turon","","1 piece (80g)",80,180,2,33,5,1.5,12,60,"Filipino,turon,banana,spring roll,dessert,street food"),
    _s("Balut","","1 piece (70g)",70,180,13,12,8,0,0,120,"Filipino,balut,duck egg,street food"),
    _s("Taho","","1 cup (250ml)",250,160,8,28,2,0.5,18,80,"Filipino,taho,tofu,sago,arnibal,breakfast,street food"),
    _s("Binatog (Boiled White Corn)","","1 cup (180g)",180,180,5,38,2,4,3,80,"Filipino,binatog,corn,street food,snack"),

    # --- Desserts & Kakanin ---
    _s("Halo-Halo","","1 regular cup (350g)",350,350,8,65,8,2,40,120,"Filipino,halo-halo,dessert,shaved ice"),
    _s("Leche Flan","","1 slice (100g)",100,200,5,30,7,0,25,80,"Filipino,leche flan,dessert,caramel,egg"),
    _s("Biko","","1 piece (80g)",80,220,2,45,4,0.5,18,60,"Filipino,biko,sticky rice,kakanin,dessert"),
    _s("Bibingka","","1 piece (100g)",100,250,5,40,8,0.5,15,180,"Filipino,bibingka,rice cake,Christmas,kakanin"),
    _s("Puto","","1 piece (40g)",40,70,2,14,1,0.3,4,80,"Filipino,puto,rice cake,kakanin,steamed"),
    _s("Kutsinta","","1 piece (40g)",40,60,1,14,0.5,0.3,5,60,"Filipino,kutsinta,rice cake,kakanin"),
    _s("Maja Blanca","","1 slice (100g)",100,180,3,35,4,1,15,80,"Filipino,maja blanca,coconut,pudding,dessert,kakanin"),
    _s("Palitaw","","1 piece (50g)",50,80,1,18,0.5,0.5,4,40,"Filipino,palitaw,rice cake,kakanin,sesame,coconut"),
    _s("Sapin-Sapin","","1 slice (80g)",80,180,2,38,3,0.5,15,60,"Filipino,sapin-sapin,layered rice cake,kakanin"),
    _s("Puto Bumbong","","2 pieces (80g)",80,140,2,30,2,1,8,80,"Filipino,puto bumbong,purple rice cake,Christmas,kakanin"),
    _s("Suman","","1 piece (80g)",80,160,2,35,2,1,2,40,"Filipino,suman,sticky rice,banana leaf,kakanin"),

    # --- Beverages ---
    _s("Buko Juice (Fresh Coconut Water)","","1 cup (240ml)",240,46,2,9,0.5,0,9,250,"Filipino,buko,coconut water,drink"),
    _s("Calamansi Juice","","1 glass (250ml)",250,50,0.5,13,0,0.5,10,10,"Filipino,calamansi,juice,citrus,drink"),
    _s("Gulaman at Sago","","1 glass (350ml)",350,180,1,44,0,0.5,35,30,"Filipino,gulaman,sago,drink,iced"),
    _s("Milo (1 sachet hot/iced)","Nestle","1 sachet (200ml)",200,110,3,20,2,1,10,90,"Filipino,milo,chocolate drink,Nestle"),
    _s("Kapeng Barako (Black Coffee)","","1 cup (240ml)",240,5,0.5,0,0,0,0,5,"Filipino,barako,coffee,black coffee,Batangas"),
    _s("Salabat (Ginger Tea)","","1 cup (240ml)",240,40,0,10,0,0,8,10,"Filipino,salabat,ginger tea,drink"),

    # --- Jollibee ---
    _s("Jollibee Chickenjoy (Thigh/Leg)","Jollibee","1 piece (~150g)",150,320,23,14,18,0.5,0,540,"Filipino fast food,Jollibee,fried chicken,Chickenjoy"),
    _s("Jollibee Chickenjoy (Breast)","Jollibee","1 piece (~180g)",180,340,31,13,18,0.5,0,580,"Filipino fast food,Jollibee,fried chicken,Chickenjoy"),
    _s("Jollibee Jolly Spaghetti (Regular)","Jollibee","1 serving (260g)",260,560,17,88,16,2,20,680,"Filipino fast food,Jollibee,spaghetti,pasta"),
    _s("Jollibee Yum! Burger","Jollibee","1 burger (~130g)",130,350,16,35,16,1,5,540,"Filipino fast food,Jollibee,burger"),
    _s("Jollibee Burger Steak","Jollibee","1 piece (180g)",180,370,22,30,16,1,4,720,"Filipino fast food,Jollibee,burger steak,gravy"),
    _s("Jollibee Peach Mango Pie","Jollibee","1 piece (85g)",85,240,2,37,9,1,12,190,"Filipino fast food,Jollibee,peach mango pie,dessert"),
    _s("Jollibee Palabok Fiesta (Regular)","Jollibee","1 serving (250g)",250,450,20,65,12,1,4,720,"Filipino fast food,Jollibee,palabok,noodles"),
    _s("Jollibee Crispy Chicken Sandwich","Jollibee","1 sandwich (~180g)",180,430,22,48,16,2,6,740,"Filipino fast food,Jollibee,chicken sandwich"),
    _s("Jollibee Jolly Hotdog","Jollibee","1 piece (~160g)",160,380,14,40,18,1,8,720,"Filipino fast food,Jollibee,hotdog"),
    _s("Jollibee Aloha Burger","Jollibee","1 burger (~180g)",180,480,22,50,20,2,10,680,"Filipino fast food,Jollibee,burger,pineapple"),
    _s("Jollibee Float (Regular)","Jollibee","1 cup (400ml)",400,290,4,50,8,0,42,100,"Filipino fast food,Jollibee,float,ice cream,drink"),

    # --- Andok's ---
    _s("Andok's Lechon Manok (1/4 Chicken)","Andok's","1/4 chicken (~200g)",200,350,35,2,22,0,1,580,"Filipino fast food,Andok's,lechon manok,roasted chicken"),
    _s("Andok's Liempo","Andok's","100g",100,300,20,3,24,0,2,480,"Filipino fast food,Andok's,liempo,pork belly,grilled"),
    _s("Andok's Pork BBQ (Stick)","Andok's","1 stick (~80g)",80,180,14,10,10,0,6,380,"Filipino fast food,Andok's,pork BBQ"),
    _s("Andok's Bagnet","Andok's","100g",100,380,22,5,31,0,0,420,"Filipino fast food,Andok's,bagnet,Ilocano,fried pork"),

    # --- Mang Inasal ---
    _s("Mang Inasal Chicken Inasal Paa","Mang Inasal","1 piece (~200g)",200,310,25,5,22,0,2,560,"Filipino fast food,Mang Inasal,chicken inasal,paa,thigh,grilled"),
    _s("Mang Inasal Chicken Inasal Pecho","Mang Inasal","1 piece (~200g)",200,290,32,4,16,0,2,520,"Filipino fast food,Mang Inasal,chicken inasal,pecho,breast,grilled"),
    _s("Mang Inasal Chicken BBQ","Mang Inasal","1 piece (~200g)",200,280,24,6,18,0,4,540,"Filipino fast food,Mang Inasal,chicken BBQ"),
    _s("Mang Inasal Pork BBQ (Stick)","Mang Inasal","1 stick (~80g)",80,200,14,8,12,0,5,420,"Filipino fast food,Mang Inasal,pork BBQ"),

    # --- Chowking ---
    _s("Chowking Lauriat Fried Chicken (full set)","Chowking","1 full set (~500g)",500,850,40,100,28,3,8,1200,"Filipino fast food,Chowking,lauriat,fried chicken"),
    _s("Chowking Beef Wonton Noodle Soup","Chowking","1 bowl (450ml)",450,350,20,48,8,2,3,980,"Filipino fast food,Chowking,wonton,noodle soup,beef"),
    _s("Chowking Chao Fan","Chowking","1 serving (200g)",200,350,10,55,10,1,2,680,"Filipino fast food,Chowking,chao fan,fried rice"),
    _s("Chowking Halo-Halo","Chowking","1 cup (400ml)",400,380,9,72,8,2,48,140,"Filipino fast food,Chowking,halo-halo,dessert"),
    _s("Chowking Siopao Asado","Chowking","1 piece (~100g)",100,240,8,38,6,1,5,340,"Filipino fast food,Chowking,siopao,asado,steamed bun"),
    _s("Chowking Hakaw Dim Sum (3pcs)","Chowking","3 pieces (~75g)",75,150,8,20,4,1,1,380,"Filipino fast food,Chowking,dim sum,hakaw,shrimp dumpling"),

    # --- Max's Restaurant ---
    _s("Max's Fried Chicken (1/4 chicken)","Max's Restaurant","1/4 chicken (~200g)",200,380,28,15,22,0.5,1,600,"Filipino fast food,Max's,fried chicken"),

    # --- Greenwich ---
    _s("Greenwich Hawaiian Overload Pizza (1 slice)","Greenwich","1 slice (~100g)",100,280,12,35,10,1.5,6,520,"Filipino fast food,Greenwich,pizza,Hawaiian"),
    _s("Greenwich Lasagna","Greenwich","1 slice (~200g)",200,380,18,42,14,2,8,640,"Filipino fast food,Greenwich,lasagna,pasta"),

    # --- Convenience Stores ---
    _s("Ministop Crispy Chicken","Ministop","1 piece (~130g)",130,360,22,22,20,0.5,1,560,"Filipino convenience store,Ministop,fried chicken"),
    _s("7-Eleven PH Hotdog on a Bun","7-Eleven Philippines","1 piece (~120g)",120,280,12,30,12,1,4,680,"Filipino convenience store,7-Eleven,hotdog"),

    # --- Condiments ---
    _s("Bagoong (Shrimp Paste)","","1 tbsp (15g)",15,20,2,1,0.5,0,0,900,"Filipino,bagoong,shrimp paste,condiment"),
    _s("Patis (Fish Sauce)","","1 tbsp (15ml)",15,5,1,0.5,0,0,0,1400,"Filipino,patis,fish sauce,condiment"),
    _s("Banana Ketchup","","1 tbsp (15g)",15,20,0,5,0,0,4,80,"Filipino,banana ketchup,condiment,Jufran"),
    _s("Sukang Maasim (Cane Vinegar)","","1 tbsp (15ml)",15,3,0,0.5,0,0,0,1,"Filipino,vinegar,suka,condiment"),
]

# === Database Helpers ==========================================================

def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


@contextmanager
def _db():
    conn = _get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _init_db():
    with _db() as conn:
        conn.executescript(DDL)
        if conn.execute("SELECT COUNT(*) FROM daily_goals").fetchone()[0] == 0:
            conn.execute(
                "INSERT INTO daily_goals (calories, protein_g, carbs_g, fat_g, fiber_g) VALUES (?,?,?,?,?)",
                (DEFAULT_GOALS["calories"], DEFAULT_GOALS["protein_g"],
                 DEFAULT_GOALS["carbs_g"],  DEFAULT_GOALS["fat_g"], DEFAULT_GOALS["fiber_g"]),
            )


def _seed_ph_foods():
    """Insert PH seed foods if missing. Idempotent — safe to call every startup."""
    with _db() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM food_database WHERE source='seeded'"
        ).fetchone()[0]
        if count >= len(PH_SEED_FOODS):
            return
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        for item in PH_SEED_FOODS:
            exists = conn.execute(
                "SELECT id FROM food_database WHERE source='seeded' AND food_name=?",
                (item["food_name"],)
            ).fetchone()
            if exists:
                continue
            conn.execute(
                """INSERT INTO food_database
                   (external_id,source,food_name,brand,serving_size,serving_g,
                    calories,protein_g,carbs_g,fat_g,fiber_g,sugar_g,sodium_mg,
                    tags,created_at,updated_at)
                   VALUES ('','seeded',?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (item["food_name"], item["brand"], item["serving_size"], item["serving_g"],
                 item["calories"], item["protein_g"], item["carbs_g"], item["fat_g"],
                 item["fiber_g"], item["sugar_g"], item["sodium_mg"], item["tags"], now, now),
            )
    logger.info("PH food seed: %d entries ensured.", len(PH_SEED_FOODS))


# === Pydantic Models ===========================================================

VALID_MEAL_TYPES = {"breakfast", "lunch", "dinner", "snack"}


class FoodLogCreate(BaseModel):
    date:         str
    time:         str   = "00:00:00"
    meal_type:    str   = "snack"
    food_name:    str
    serving_size: str   = "1 serving"
    calories:     float = 0.0
    protein_g:    float = 0.0
    carbs_g:      float = 0.0
    fat_g:        float = 0.0
    fiber_g:      float = 0.0
    sugar_g:      float = 0.0
    sodium_mg:    float = 0.0
    notes:        str   = ""


class FoodLogUpdate(BaseModel):
    date:         Optional[str]   = None
    time:         Optional[str]   = None
    meal_type:    Optional[str]   = None
    food_name:    Optional[str]   = None
    serving_size: Optional[str]   = None
    calories:     Optional[float] = None
    protein_g:    Optional[float] = None
    carbs_g:      Optional[float] = None
    fat_g:        Optional[float] = None
    fiber_g:      Optional[float] = None
    sugar_g:      Optional[float] = None
    sodium_mg:    Optional[float] = None
    notes:        Optional[str]   = None


class GoalsUpdate(BaseModel):
    calories:  Optional[float] = None
    protein_g: Optional[float] = None
    carbs_g:   Optional[float] = None
    fat_g:     Optional[float] = None
    fiber_g:   Optional[float] = None


class FoodDBCreate(BaseModel):
    food_name:    str
    brand:        str   = ""
    serving_size: str   = "100g"
    serving_g:    float = 100.0
    calories:     float = 0.0
    protein_g:    float = 0.0
    carbs_g:      float = 0.0
    fat_g:        float = 0.0
    fiber_g:      float = 0.0
    sugar_g:      float = 0.0
    sodium_mg:    float = 0.0
    tags:         str   = ""


class FoodDBUpdate(BaseModel):
    food_name:    Optional[str]   = None
    brand:        Optional[str]   = None
    serving_size: Optional[str]   = None
    serving_g:    Optional[float] = None
    calories:     Optional[float] = None
    protein_g:    Optional[float] = None
    carbs_g:      Optional[float] = None
    fat_g:        Optional[float] = None
    fiber_g:      Optional[float] = None
    sugar_g:      Optional[float] = None
    sodium_mg:    Optional[float] = None
    tags:         Optional[str]   = None


class QuickLogCreate(BaseModel):
    food_id:   int
    meal_type: str
    date:      str           = ""
    time:      str           = "00:00:00"
    servings:  float         = 1.0
    grams:     Optional[float] = None
    notes:     str           = ""


# === Utilities =================================================================

def _row_to_dict(row: sqlite3.Row) -> dict:
    return dict(row)


def _today_ph() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d")


# === External API: Open Food Facts ============================================

def _normalize_off_product(product: dict) -> Optional[dict]:
    name = (product.get("product_name") or "").strip()
    if not name:
        return None
    n = product.get("nutriments") or {}
    serving_size = (product.get("serving_size") or "100g").strip()
    try:
        serving_g = float(product.get("serving_quantity") or 0) or 100.0
    except (TypeError, ValueError):
        serving_g = 100.0

    if n.get("energy-kcal_serving") is not None and serving_g != 100.0:
        sfx = "_serving"
    else:
        sfx = "_100g"
        serving_g = 100.0
        serving_size = "100g"

    def _n(key: str) -> float:
        v = n.get(key + sfx) or n.get(key + "_100g") or 0.0
        try:
            return round(float(v), 2)
        except (TypeError, ValueError):
            return 0.0

    calories = _n("energy-kcal")
    if calories == 0.0:
        kj = _n("energy")
        calories = round(kj / 4.184, 2) if kj else 0.0

    sodium_g  = _n("sodium")
    sodium_mg = round(sodium_g * 1000, 2) if sodium_g else 0.0

    raw_tags = product.get("categories_tags") or []
    tags = ", ".join(
        t.replace("en:", "").replace("-", " ")
        for t in raw_tags[:6]
        if t.startswith("en:")
    )

    return dict(
        external_id  = str(product.get("code") or product.get("_id") or ""),
        source       = "openfoodfacts",
        food_name    = name,
        brand        = (product.get("brands") or "").strip(),
        serving_size = serving_size,
        serving_g    = serving_g,
        calories     = calories,
        protein_g    = _n("proteins"),
        carbs_g      = _n("carbohydrates"),
        fat_g        = _n("fat"),
        fiber_g      = _n("fiber"),
        sugar_g      = _n("sugars"),
        sodium_mg    = sodium_mg,
        tags         = tags,
    )


async def _search_off(query: str, limit: int) -> list:
    params = {
        "action":       "process",
        "search_terms": query,
        "json":         "1",
        "page_size":    str(limit),
        "sort_by":      "unique_scans_n",
        "fields":       "product_name,brands,nutriments,serving_size,serving_quantity,categories_tags,code",
    }
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.get(
                f"{OFF_BASE}/cgi/search.pl",
                params=params,
                headers={"User-Agent": "ClawdBot-NutritionTracker/1.0 (personal, Paolo PH)"},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning("Open Food Facts search failed for %r: %s", query, exc)
        return []

    results = []
    for product in (data.get("products") or []):
        item = _normalize_off_product(product)
        if item and item["calories"] > 0:
            results.append(item)
    return results


# === External API: USDA FoodData Central ======================================

def _normalize_usda_food(food: dict) -> Optional[dict]:
    name = (food.get("description") or "").strip()
    if not name:
        return None
    nmap: dict = {}
    for fn in (food.get("foodNutrients") or []):
        nid = fn.get("nutrientId") or fn.get("nutrientNumber")
        val = fn.get("value") or fn.get("amount") or 0.0
        if nid:
            nmap[str(nid)] = float(val)

    def _g(*ids) -> float:
        for nid in ids:
            v = nmap.get(str(nid))
            if v is not None:
                return round(float(v), 2)
        return 0.0

    return dict(
        external_id  = str(food.get("fdcId") or ""),
        source       = "usda",
        food_name    = name,
        brand        = (food.get("brandOwner") or food.get("brandName") or "").strip(),
        serving_size = "100g",
        serving_g    = 100.0,
        calories     = _g(1008),
        protein_g    = _g(1003),
        carbs_g      = _g(1005),
        fat_g        = _g(1004),
        fiber_g      = _g(1079),
        sugar_g      = _g(2000),
        sodium_mg    = _g(1093),
        tags         = (food.get("foodCategory") or "").strip(),
    )


async def _search_usda(query: str, limit: int) -> list:
    params = {
        "query":    query,
        "pageSize": limit,
        "api_key":  USDA_KEY,
        "dataType": "Foundation,SR Legacy,Branded",
    }
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.get(f"{USDA_BASE}/foods/search", params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning("USDA search failed for %r: %s", query, exc)
        return []

    results = []
    for food in (data.get("foods") or []):
        item = _normalize_usda_food(food)
        if item and item["calories"] > 0:
            results.append(item)
    return results


def _cache_food(conn: sqlite3.Connection, item: dict) -> dict:
    """Upsert an external food item into local DB. Returns the saved row."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    existing = None
    if item["external_id"]:
        existing = conn.execute(
            "SELECT id FROM food_database WHERE external_id=? AND source=?",
            (item["external_id"], item["source"]),
        ).fetchone()

    if existing:
        conn.execute(
            """UPDATE food_database
               SET food_name=?,brand=?,serving_size=?,serving_g=?,
                   calories=?,protein_g=?,carbs_g=?,fat_g=?,
                   fiber_g=?,sugar_g=?,sodium_mg=?,tags=?,updated_at=?
               WHERE id=?""",
            (item["food_name"], item["brand"], item["serving_size"], item["serving_g"],
             item["calories"], item["protein_g"], item["carbs_g"], item["fat_g"],
             item["fiber_g"], item["sugar_g"], item["sodium_mg"], item["tags"], now,
             existing["id"]),
        )
        row = conn.execute("SELECT * FROM food_database WHERE id=?", (existing["id"],)).fetchone()
    else:
        cur = conn.execute(
            """INSERT INTO food_database
               (external_id,source,food_name,brand,serving_size,serving_g,
                calories,protein_g,carbs_g,fat_g,fiber_g,sugar_g,sodium_mg,
                tags,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (item["external_id"], item["source"], item["food_name"], item["brand"],
             item["serving_size"], item["serving_g"], item["calories"], item["protein_g"],
             item["carbs_g"], item["fat_g"], item["fiber_g"], item["sugar_g"],
             item["sodium_mg"], item["tags"], now, now),
        )
        row = conn.execute("SELECT * FROM food_database WHERE id=?", (cur.lastrowid,)).fetchone()
    return _row_to_dict(row)


# === Startup ==================================================================

@app.on_event("startup")
async def startup():
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    _init_db()
    _seed_ph_foods()


# === Static UI ================================================================

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def index():
    html_path = Path("/app/index.html")
    if html_path.exists():
        return HTMLResponse(html_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Nutrition Tracker not found</h1>")


# === Food Database Routes =====================================================

@app.get("/api/foods/search")
async def search_foods(
    q:      str = Query(..., min_length=1, description="Food name / keyword"),
    limit:  int = Query(15, ge=1, le=50),
    source: str = Query("all", description="all | local | seeded | openfoodfacts | usda | custom"),
):
    """
    Search the food database (3-tier):
    1. Local SQLite first (seeded PH data + previously cached external results).
    2. Open Food Facts (global, no API key).
    3. USDA FoodData Central (generic/raw foods).
    External results are cached locally for future instant lookups.
    """
    src_filter = ""
    src_params: list = []
    if source not in ("all", "local"):
        src_filter = " AND source = ?"
        src_params = [source]

    with _db() as conn:
        rows = conn.execute(
            f"""SELECT * FROM food_database
                WHERE food_name LIKE ?{src_filter}
                ORDER BY
                  CASE WHEN LOWER(food_name)=LOWER(?) THEN 0
                       WHEN LOWER(food_name) LIKE LOWER(?) THEN 1
                       ELSE 2 END,
                  (source='seeded') DESC,
                  food_name ASC
                LIMIT ?""",
            [f"%{q}%"] + src_params + [q, f"{q}%", limit],
        ).fetchall()
    results = [_row_to_dict(r) for r in rows]

    if source == "all" and len(results) < limit:
        existing = {(r["external_id"], r["source"]) for r in results}
        need = limit - len(results)
        off_raw = await _search_off(q, need + 5)
        new_off = [i for i in off_raw if (i["external_id"], i["source"]) not in existing][:need]
        if new_off:
            with _db() as conn:
                for item in new_off:
                    results.append(_cache_food(conn, item))
                    existing.add((item["external_id"], item["source"]))

    if source == "all" and len(results) < limit:
        existing = {(r["external_id"], r["source"]) for r in results}
        need = limit - len(results)
        usda_raw = await _search_usda(q, need + 5)
        new_usda = [i for i in usda_raw if (i["external_id"], i["source"]) not in existing][:need]
        if new_usda:
            with _db() as conn:
                for item in new_usda:
                    results.append(_cache_food(conn, item))

    return {"query": q, "total": len(results), "items": results[:limit]}


@app.get("/api/foods/barcode/{barcode}")
async def lookup_barcode(barcode: str):
    """Look up a food by EAN/UPC barcode via Open Food Facts. Results are cached locally."""
    with _db() as conn:
        row = conn.execute(
            "SELECT * FROM food_database WHERE external_id=? AND source='openfoodfacts'",
            (barcode,),
        ).fetchone()
    if row:
        return _row_to_dict(row)

    url = (
        f"{OFF_BASE}/api/v2/product/{barcode}"
        "?fields=product_name,brands,nutriments,serving_size,serving_quantity,categories_tags,code"
    )
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.get(url, headers={"User-Agent": "ClawdBot-NutritionTracker/1.0"})
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Open Food Facts error: {exc}")

    if data.get("status") != 1:
        raise HTTPException(status_code=404, detail="Product not found in Open Food Facts")

    product = data.get("product") or {}
    product["code"] = barcode
    item = _normalize_off_product(product)
    if not item:
        raise HTTPException(status_code=422, detail="Could not parse product nutrition data")

    with _db() as conn:
        return _cache_food(conn, item)


@app.get("/api/foods")
async def list_foods(
    source:   str = Query("", description="Filter by source (seeded, custom, openfoodfacts, usda)"),
    search:   str = Query("", description="Search food_name"),
    page:     int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
):
    where: list[str] = []
    params: list = []
    if source:
        where.append("source = ?")
        params.append(source)
    if search:
        where.append("food_name LIKE ?")
        params.append(f"%{search}%")
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    offset = (page - 1) * per_page
    with _db() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM food_database {where_sql}", params).fetchone()[0]
        rows  = conn.execute(
            f"SELECT * FROM food_database {where_sql} ORDER BY food_name ASC LIMIT ? OFFSET ?",
            params + [per_page, offset],
        ).fetchall()
    return {
        "total": total, "page": page, "per_page": per_page,
        "pages": (total + per_page - 1) // per_page if total else 0,
        "items": [_row_to_dict(r) for r in rows],
    }


@app.get("/api/foods/{food_id}")
async def get_food(food_id: int):
    with _db() as conn:
        row = conn.execute("SELECT * FROM food_database WHERE id=?", (food_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Food not found")
    return _row_to_dict(row)


@app.post("/api/foods", status_code=201)
async def create_custom_food(body: FoodDBCreate):
    if not body.food_name.strip():
        raise HTTPException(status_code=422, detail="food_name is required")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with _db() as conn:
        cur = conn.execute(
            """INSERT INTO food_database
               (external_id,source,food_name,brand,serving_size,serving_g,
                calories,protein_g,carbs_g,fat_g,fiber_g,sugar_g,sodium_mg,
                tags,created_at,updated_at)
               VALUES ('','custom',?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (body.food_name, body.brand, body.serving_size, body.serving_g,
             body.calories, body.protein_g, body.carbs_g, body.fat_g,
             body.fiber_g, body.sugar_g, body.sodium_mg, body.tags, now, now),
        )
        row = conn.execute("SELECT * FROM food_database WHERE id=?", (cur.lastrowid,)).fetchone()
    return _row_to_dict(row)


@app.put("/api/foods/{food_id}")
async def update_food(food_id: int, body: FoodDBUpdate):
    with _db() as conn:
        existing = conn.execute("SELECT * FROM food_database WHERE id=?", (food_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Food not found")
        updates = {k: v for k, v in body.dict().items() if v is not None}
        if not updates:
            return _row_to_dict(existing)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        updates["updated_at"] = now
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        conn.execute(
            f"UPDATE food_database SET {set_clause} WHERE id=?",
            list(updates.values()) + [food_id],
        )
        row = conn.execute("SELECT * FROM food_database WHERE id=?", (food_id,)).fetchone()
    return _row_to_dict(row)


@app.delete("/api/foods/{food_id}")
async def delete_food(food_id: int):
    with _db() as conn:
        if not conn.execute("SELECT id FROM food_database WHERE id=?", (food_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Food not found")
        conn.execute("DELETE FROM food_database WHERE id=?", (food_id,))
    return {"deleted": True, "id": food_id}


@app.post("/api/log/quick", status_code=201)
async def quick_log_food(body: QuickLogCreate):
    """
    Log food from the food database — auto-fills all nutrition data.
    Specify food_id + servings (multiplier, default 1.0) OR grams.
    """
    if body.meal_type not in VALID_MEAL_TYPES:
        raise HTTPException(status_code=422, detail=f"meal_type must be one of {sorted(VALID_MEAL_TYPES)}")
    if body.servings <= 0 and body.grams is None:
        raise HTTPException(status_code=422, detail="servings must be > 0")

    with _db() as conn:
        food = conn.execute("SELECT * FROM food_database WHERE id=?", (body.food_id,)).fetchone()
        if not food:
            raise HTTPException(status_code=404, detail=f"Food id={body.food_id} not found in food database")
        food = _row_to_dict(food)

        if body.grams is not None and food["serving_g"] > 0:
            mult = body.grams / food["serving_g"]
            serving_desc = f"{body.grams:.0f}g"
        elif body.servings != 1.0:
            mult = body.servings
            serving_desc = f"{body.servings:g} x {food['serving_size']}"
        else:
            mult = 1.0
            serving_desc = food["serving_size"]

        def _scale(val: Any) -> float:
            return round(float(val) * mult, 2)

        brand_sfx    = f" ({food['brand']})" if food["brand"] else ""
        target_date  = body.date or _today_ph()
        now          = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        cur = conn.execute(
            """INSERT INTO food_log
               (date,time,meal_type,food_name,serving_size,
                calories,protein_g,carbs_g,fat_g,fiber_g,sugar_g,sodium_mg,
                notes,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (target_date, body.time, body.meal_type,
             food["food_name"] + brand_sfx, serving_desc,
             _scale(food["calories"]), _scale(food["protein_g"]), _scale(food["carbs_g"]),
             _scale(food["fat_g"]), _scale(food["fiber_g"]), _scale(food["sugar_g"]),
             _scale(food["sodium_mg"]), body.notes, now, now),
        )
        row = conn.execute("SELECT * FROM food_log WHERE id=?", (cur.lastrowid,)).fetchone()
    return _row_to_dict(row)


# === Food Log CRUD ============================================================

@app.get("/api/log")
async def list_log(
    date_from: str = Query("", description="Start date YYYY-MM-DD"),
    date_to:   str = Query("", description="End date YYYY-MM-DD"),
    date:      str = Query("", description="Exact date YYYY-MM-DD"),
    meal_type: str = Query("", description="Filter by meal_type"),
    search:    str = Query("", description="Search in food_name/notes"),
    page:      int = Query(1, ge=1),
    per_page:  int = Query(100, ge=1, le=500),
    sort:      str = Query("date_desc"),
):
    where_clauses: list[str] = []
    params: list[Any] = []

    if date:
        where_clauses.append("date = ?")
        params.append(date)
    else:
        if date_from:
            where_clauses.append("date >= ?")
            params.append(date_from)
        if date_to:
            where_clauses.append("date <= ?")
            params.append(date_to)
    if meal_type:
        where_clauses.append("meal_type = ?")
        params.append(meal_type)
    if search:
        where_clauses.append("(food_name LIKE ? OR notes LIKE ?)")
        params += [f"%{search}%", f"%{search}%"]

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    sort_map = {
        "date_desc":     "date DESC, time DESC",
        "date_asc":      "date ASC, time ASC",
        "calories_desc": "calories DESC",
        "calories_asc":  "calories ASC",
    }
    order_sql = sort_map.get(sort, "date DESC, time DESC")
    offset = (page - 1) * per_page

    with _db() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM food_log {where_sql}", params).fetchone()[0]
        rows  = conn.execute(
            f"SELECT * FROM food_log {where_sql} ORDER BY {order_sql} LIMIT ? OFFSET ?",
            params + [per_page, offset],
        ).fetchall()

    return {
        "total":    total,
        "page":     page,
        "per_page": per_page,
        "pages":    (total + per_page - 1) // per_page if total else 0,
        "items":    [_row_to_dict(r) for r in rows],
    }


@app.get("/api/log/{entry_id}")
async def get_log_entry(entry_id: int):
    with _db() as conn:
        row = conn.execute("SELECT * FROM food_log WHERE id=?", (entry_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Entry not found")
    return _row_to_dict(row)


@app.post("/api/log", status_code=201)
async def create_log_entry(body: FoodLogCreate):
    if not body.food_name.strip():
        raise HTTPException(status_code=422, detail="food_name is required")
    if body.meal_type not in VALID_MEAL_TYPES:
        raise HTTPException(status_code=422, detail=f"meal_type must be one of {sorted(VALID_MEAL_TYPES)}")
    if body.calories < 0:
        raise HTTPException(status_code=422, detail="calories cannot be negative")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with _db() as conn:
        cur = conn.execute(
            """INSERT INTO food_log
               (date,time,meal_type,food_name,serving_size,
                calories,protein_g,carbs_g,fat_g,fiber_g,sugar_g,sodium_mg,
                notes,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (body.date, body.time, body.meal_type, body.food_name, body.serving_size,
             body.calories, body.protein_g, body.carbs_g, body.fat_g,
             body.fiber_g, body.sugar_g, body.sodium_mg, body.notes, now, now),
        )
        row = conn.execute("SELECT * FROM food_log WHERE id=?", (cur.lastrowid,)).fetchone()
    return _row_to_dict(row)


@app.put("/api/log/{entry_id}")
async def update_log_entry(entry_id: int, body: FoodLogUpdate):
    with _db() as conn:
        existing = conn.execute("SELECT * FROM food_log WHERE id=?", (entry_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Entry not found")
        updates = {k: v for k, v in body.dict().items() if v is not None}
        if not updates:
            return _row_to_dict(existing)
        if "meal_type" in updates and updates["meal_type"] not in VALID_MEAL_TYPES:
            raise HTTPException(status_code=422, detail=f"meal_type must be one of {sorted(VALID_MEAL_TYPES)}")
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        updates["updated_at"] = now
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        conn.execute(
            f"UPDATE food_log SET {set_clause} WHERE id=?",
            list(updates.values()) + [entry_id],
        )
        row = conn.execute("SELECT * FROM food_log WHERE id=?", (entry_id,)).fetchone()
    return _row_to_dict(row)


@app.delete("/api/log/{entry_id}")
async def delete_log_entry(entry_id: int):
    with _db() as conn:
        if not conn.execute("SELECT id FROM food_log WHERE id=?", (entry_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Entry not found")
        conn.execute("DELETE FROM food_log WHERE id=?", (entry_id,))
    return {"deleted": True, "id": entry_id}


# === Summary / Analytics ======================================================

def _macro_sum(conn, date_filter: str, params: list) -> dict:
    row = conn.execute(
        f"""SELECT
               COALESCE(SUM(calories),  0) AS calories,
               COALESCE(SUM(protein_g), 0) AS protein_g,
               COALESCE(SUM(carbs_g),   0) AS carbs_g,
               COALESCE(SUM(fat_g),     0) AS fat_g,
               COALESCE(SUM(fiber_g),   0) AS fiber_g,
               COALESCE(SUM(sugar_g),   0) AS sugar_g,
               COALESCE(SUM(sodium_mg), 0) AS sodium_mg,
               COUNT(*) AS entry_count
            FROM food_log {date_filter}""",
        params,
    ).fetchone()
    return {k: round(row[k], 1) for k in row.keys()}


@app.get("/api/summary")
async def daily_summary(day: str = Query("", description="YYYY-MM-DD, defaults to today (UTC+8)")):
    target_date = day or _today_ph()
    with _db() as conn:
        totals = _macro_sum(conn, "WHERE date = ?", [target_date])
        meal_rows = conn.execute(
            """SELECT meal_type,
                      COALESCE(SUM(calories),  0) AS calories,
                      COALESCE(SUM(protein_g), 0) AS protein_g,
                      COALESCE(SUM(carbs_g),   0) AS carbs_g,
                      COALESCE(SUM(fat_g),     0) AS fat_g,
                      COUNT(*) AS entry_count
               FROM food_log WHERE date = ?
               GROUP BY meal_type ORDER BY meal_type""",
            (target_date,),
        ).fetchall()
        goals_row = conn.execute(
            "SELECT * FROM daily_goals ORDER BY id DESC LIMIT 1"
        ).fetchone()
    goals  = _row_to_dict(goals_row) if goals_row else DEFAULT_GOALS
    meals  = {r["meal_type"]: {k: round(r[k], 1) for k in r.keys() if k != "meal_type"} for r in meal_rows}
    pct    = lambda a, g: round(min(a / g * 100, 999), 1) if g else 0
    remaining = {k: round(goals[k] - totals[k], 1) for k in ("calories", "protein_g", "carbs_g", "fat_g")}
    return {
        "date":      target_date,
        "totals":    totals,
        "goals":     goals,
        "meals":     meals,
        "remaining": remaining,
        "progress":  {
            "calories_pct": pct(totals["calories"],  goals["calories"]),
            "protein_pct":  pct(totals["protein_g"], goals["protein_g"]),
            "carbs_pct":    pct(totals["carbs_g"],   goals["carbs_g"]),
            "fat_pct":      pct(totals["fat_g"],     goals["fat_g"]),
            "fiber_pct":    pct(totals["fiber_g"],   goals["fiber_g"]),
        },
    }


@app.get("/api/weekly-trend")
async def weekly_trend(weeks: int = Query(4, ge=1, le=26)):
    end   = _today_ph()
    start = (datetime.strptime(end, "%Y-%m-%d") - timedelta(days=weeks * 7 - 1)).strftime("%Y-%m-%d")
    with _db() as conn:
        rows = conn.execute(
            """SELECT date,
                      COALESCE(SUM(calories),  0) AS calories,
                      COALESCE(SUM(protein_g), 0) AS protein_g,
                      COALESCE(SUM(carbs_g),   0) AS carbs_g,
                      COALESCE(SUM(fat_g),     0) AS fat_g,
                      COALESCE(SUM(fiber_g),   0) AS fiber_g,
                      COUNT(*) AS entry_count
               FROM food_log WHERE date >= ? AND date <= ?
               GROUP BY date ORDER BY date ASC""",
            (start, end),
        ).fetchall()
        goals_row = conn.execute("SELECT * FROM daily_goals ORDER BY id DESC LIMIT 1").fetchone()
    goals = _row_to_dict(goals_row) if goals_row else DEFAULT_GOALS
    return {
        "start": start, "end": end, "goals": goals,
        "data":  [{k: round(r[k], 1) if k != "date" else r[k] for k in r.keys()} for r in rows],
    }


@app.get("/api/goals")
async def get_goals():
    with _db() as conn:
        row = conn.execute("SELECT * FROM daily_goals ORDER BY id DESC LIMIT 1").fetchone()
    return _row_to_dict(row) if row else DEFAULT_GOALS


@app.put("/api/goals")
async def update_goals(body: GoalsUpdate):
    updates = {k: v for k, v in body.dict().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=422, detail="No fields provided")
    for k, v in updates.items():
        if v < 0:
            raise HTTPException(status_code=422, detail=f"{k} cannot be negative")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with _db() as conn:
        existing = conn.execute("SELECT * FROM daily_goals ORDER BY id DESC LIMIT 1").fetchone()
        if existing:
            merged = dict(existing)
            merged.update(updates)
            conn.execute(
                "UPDATE daily_goals SET calories=?,protein_g=?,carbs_g=?,fat_g=?,fiber_g=? WHERE id=?",
                (merged["calories"], merged["protein_g"], merged["carbs_g"],
                 merged["fat_g"], merged["fiber_g"], merged["id"]),
            )
            row = conn.execute("SELECT * FROM daily_goals WHERE id=?", (merged["id"],)).fetchone()
        else:
            vals = {**DEFAULT_GOALS, **updates}
            cur  = conn.execute(
                "INSERT INTO daily_goals (calories,protein_g,carbs_g,fat_g,fiber_g,created_at) VALUES (?,?,?,?,?,?)",
                (vals["calories"], vals["protein_g"], vals["carbs_g"],
                 vals["fat_g"], vals["fiber_g"], now),
            )
            row = conn.execute("SELECT * FROM daily_goals WHERE id=?", (cur.lastrowid,)).fetchone()
    return _row_to_dict(row)


@app.get("/api/export/csv")
async def export_csv():
    with _db() as conn:
        rows = conn.execute(
            """SELECT id,date,time,meal_type,food_name,serving_size,
                      calories,protein_g,carbs_g,fat_g,fiber_g,sugar_g,sodium_mg,notes
               FROM food_log ORDER BY date DESC, time DESC"""
        ).fetchall()

    def _generate():
        yield "id,date,time,meal_type,food_name,serving_size,calories,protein_g,carbs_g,fat_g,fiber_g,sugar_g,sodium_mg,notes\n"
        for row in rows:
            vals = [str(v).replace('"', '""') for v in row]
            yield ",".join(f'"{v}"' for v in vals) + "\n"

    return StreamingResponse(
        _generate(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=nutrition-export.csv"},
    )


@app.get("/api/health", include_in_schema=False)
async def health():
    with _db() as conn:
        food_count = conn.execute("SELECT COUNT(*) FROM food_database").fetchone()[0]
    return {"status": "ok", "food_database_entries": food_count}

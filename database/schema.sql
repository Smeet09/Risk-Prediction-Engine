--
-- PostgreSQL database dump
--

-- Dumped from database version 15.4 (Debian 15.4-1.pgdg110+1)
-- Dumped by pg_dump version 15.4 (Debian 15.4-1.pgdg110+1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: postgis; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS postgis WITH SCHEMA public;


--
-- Name: EXTENSION postgis; Type: COMMENT; Schema: -; Owner: 
--

COMMENT ON EXTENSION postgis IS 'PostGIS geometry and geography spatial types and functions';


--
-- Name: uuid-ossp; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA public;


--
-- Name: EXTENSION "uuid-ossp"; Type: COMMENT; Schema: -; Owner: 
--

COMMENT ON EXTENSION "uuid-ossp" IS 'generate universally unique identifiers (UUIDs)';


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: data_inventory; Type: TABLE; Schema: public; Owner: aether
--

CREATE TABLE public.data_inventory (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    region_id uuid NOT NULL,
    dem_ready boolean DEFAULT false,
    exposure_ready boolean DEFAULT false,
    manual_ready boolean DEFAULT false,
    susceptibility_ready boolean DEFAULT false,
    updated_at timestamp with time zone DEFAULT now(),
    terrain_ready boolean DEFAULT false,
    manual_india_ready boolean DEFAULT false,
    topo_ready boolean DEFAULT false
);


ALTER TABLE public.data_inventory OWNER TO aether;

--
-- Name: dem_uploads; Type: TABLE; Schema: public; Owner: aether
--

CREATE TABLE public.dem_uploads (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    region_id uuid NOT NULL,
    file_path text NOT NULL,
    file_name text,
    file_size_mb double precision,
    uploaded_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.dem_uploads OWNER TO aether;

--
-- Name: disaster_types; Type: TABLE; Schema: public; Owner: aether
--

CREATE TABLE public.disaster_types (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    name text NOT NULL,
    code text NOT NULL,
    category text NOT NULL,
    description text,
    icon text DEFAULT '⚠️'::text,
    color text DEFAULT '#0071e3'::text,
    is_active boolean DEFAULT true,
    sort_order integer DEFAULT 0,
    default_weights jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    script_path text
);


ALTER TABLE public.disaster_types OWNER TO aether;

--
-- Name: districts_boundaries; Type: TABLE; Schema: public; Owner: aether
--

CREATE TABLE public.districts_boundaries (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    name text NOT NULL,
    state_name text,
    geom public.geometry(MultiPolygon,4326),
    centroid public.geometry(Point,4326),
    bbox public.geometry(Polygon,4326),
    source_fid integer,
    created_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.districts_boundaries OWNER TO aether;

--
-- Name: india_coastlines; Type: TABLE; Schema: public; Owner: aether
--

CREATE TABLE public.india_coastlines (
    "FID" bigint,
    geom public.geometry(Geometry,4326),
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    created_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.india_coastlines OWNER TO aether;

--
-- Name: india_faults; Type: TABLE; Schema: public; Owner: aether
--

CREATE TABLE public.india_faults (
    fid double precision,
    average_di text,
    average_ra text,
    catalog_id text,
    catalog_na text,
    dip_dir text,
    lower_seis text,
    name text,
    net_slip_r text,
    slip_type text,
    upper_seis text,
    reference text,
    epistemic_ text,
    accuracy text,
    activity_c text,
    fs_name text,
    last_movem text,
    downthrown text,
    vert_sep_r text,
    strike_sli text,
    exposure_q text,
    shortening text,
    notes text,
    downthro_1 text,
    geom public.geometry(Geometry,4326),
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    created_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.india_faults OWNER TO aether;

--
-- Name: india_rivers; Type: TABLE; Schema: public; Owner: aether
--

CREATE TABLE public.india_rivers (
    "Join_Count" integer,
    "DESCR" text,
    "NAME" text,
    "F_AREA" double precision,
    "DESCR_1" text,
    "Check" text,
    "Nilam" text,
    geom public.geometry(Geometry,4326),
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    created_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.india_rivers OWNER TO aether;

--
-- Name: jobs; Type: TABLE; Schema: public; Owner: aether
--

CREATE TABLE public.jobs (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    region_id uuid,
    module text NOT NULL,
    disaster_type text,
    status text DEFAULT 'pending'::text NOT NULL,
    log text DEFAULT ''::text,
    progress integer DEFAULT 0,
    file_path text,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    country text,
    state text,
    year integer,
    month integer,
    CONSTRAINT jobs_module_check CHECK ((module = ANY (ARRAY['dem'::text, 'exposure'::text, 'manual'::text, 'susceptibility'::text, 'weather'::text]))),
    CONSTRAINT jobs_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'processing'::text, 'done'::text, 'failed'::text])))
);


ALTER TABLE public.jobs OWNER TO aether;

--
-- Name: manual_data_india; Type: TABLE; Schema: public; Owner: aether
--

CREATE TABLE public.manual_data_india (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    data_type text NOT NULL,
    file_path text NOT NULL,
    file_name text,
    description text,
    uploaded_at timestamp with time zone DEFAULT now(),
    uploaded_by uuid,
    is_local_path boolean DEFAULT false,
    postgis_imported boolean DEFAULT false,
    CONSTRAINT manual_data_india_data_type_check CHECK ((data_type = ANY (ARRAY['lulc'::text, 'river'::text, 'soil'::text, 'fault'::text, 'coastline'::text])))
);


ALTER TABLE public.manual_data_india OWNER TO aether;

--
-- Name: migrations_history; Type: TABLE; Schema: public; Owner: aether
--

CREATE TABLE public.migrations_history (
    id integer NOT NULL,
    file_name text NOT NULL,
    applied_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.migrations_history OWNER TO aether;

--
-- Name: migrations_history_id_seq; Type: SEQUENCE; Schema: public; Owner: aether
--

CREATE SEQUENCE public.migrations_history_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.migrations_history_id_seq OWNER TO aether;

--
-- Name: migrations_history_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: aether
--

ALTER SEQUENCE public.migrations_history_id_seq OWNED BY public.migrations_history.id;


--
-- Name: rainfall_timeseries; Type: TABLE; Schema: public; Owner: aether
--

CREATE TABLE public.rainfall_timeseries (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    region_id uuid NOT NULL,
    date date NOT NULL,
    hour smallint NOT NULL,
    rainfall_mm double precision,
    geom public.geometry(MultiPolygon,4326),
    created_at timestamp with time zone DEFAULT now(),
    CONSTRAINT rainfall_timeseries_hour_check CHECK (((hour >= 0) AND (hour <= 23)))
);


ALTER TABLE public.rainfall_timeseries OWNER TO aether;

--
-- Name: regions; Type: TABLE; Schema: public; Owner: aether
--

CREATE TABLE public.regions (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    country text NOT NULL,
    state text,
    district text,
    centroid public.geometry(Point,4326),
    bbox public.geometry(Polygon,4326),
    created_at timestamp with time zone DEFAULT now(),
    geom public.geometry(MultiPolygon,4326)
);


ALTER TABLE public.regions OWNER TO aether;

--
-- Name: states_boundaries; Type: TABLE; Schema: public; Owner: aether
--

CREATE TABLE public.states_boundaries (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    name text NOT NULL,
    geom public.geometry(MultiPolygon,4326),
    centroid public.geometry(Point,4326),
    bbox public.geometry(Polygon,4326),
    source_fid integer,
    created_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.states_boundaries OWNER TO aether;

--
-- Name: susceptibility_flood; Type: TABLE; Schema: public; Owner: aether
--

CREATE TABLE public.susceptibility_flood (
    id bigint NOT NULL,
    region_id text NOT NULL,
    country text DEFAULT ''::text,
    state text DEFAULT ''::text,
    district text DEFAULT ''::text,
    class_id integer,
    susceptibility text,
    geom public.geometry(MultiPolygon,4326),
    created_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.susceptibility_flood OWNER TO aether;

--
-- Name: susceptibility_flood_id_seq; Type: SEQUENCE; Schema: public; Owner: aether
--

CREATE SEQUENCE public.susceptibility_flood_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.susceptibility_flood_id_seq OWNER TO aether;

--
-- Name: susceptibility_flood_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: aether
--

ALTER SEQUENCE public.susceptibility_flood_id_seq OWNED BY public.susceptibility_flood.id;


--
-- Name: susceptibility_landslide; Type: TABLE; Schema: public; Owner: aether
--

CREATE TABLE public.susceptibility_landslide (
    id bigint NOT NULL,
    region_id text NOT NULL,
    country text DEFAULT ''::text,
    state text DEFAULT ''::text,
    district text DEFAULT ''::text,
    class_id integer,
    susceptibility text,
    geom public.geometry(MultiPolygon,4326),
    created_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.susceptibility_landslide OWNER TO aether;

--
-- Name: susceptibility_landslide_id_seq; Type: SEQUENCE; Schema: public; Owner: aether
--

CREATE SEQUENCE public.susceptibility_landslide_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.susceptibility_landslide_id_seq OWNER TO aether;

--
-- Name: susceptibility_landslide_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: aether
--

ALTER SEQUENCE public.susceptibility_landslide_id_seq OWNED BY public.susceptibility_landslide.id;


--
-- Name: susceptibility_results; Type: TABLE; Schema: public; Owner: aether
--

CREATE TABLE public.susceptibility_results (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    region_id uuid NOT NULL,
    disaster_type_id uuid,
    disaster_code text NOT NULL,
    terrain_weights jsonb,
    tif_path text,
    geojson_path text,
    final_geojson jsonb,
    class_stats jsonb,
    status text DEFAULT 'done'::text,
    log text,
    generated_at timestamp with time zone DEFAULT now(),
    dominant_class integer,
    CONSTRAINT susceptibility_results_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'processing'::text, 'done'::text, 'failed'::text])))
);


ALTER TABLE public.susceptibility_results OWNER TO aether;

--
-- Name: talukas_boundaries; Type: TABLE; Schema: public; Owner: aether
--

CREATE TABLE public.talukas_boundaries (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    name text NOT NULL,
    district_name text,
    state_name text,
    geom public.geometry(MultiPolygon,4326),
    centroid public.geometry(Point,4326),
    bbox public.geometry(Polygon,4326),
    source_fid integer,
    created_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.talukas_boundaries OWNER TO aether;

--
-- Name: terrain_classifications; Type: TABLE; Schema: public; Owner: aether
--

CREATE TABLE public.terrain_classifications (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    region_id uuid NOT NULL,
    tif_path text,
    shp_path text,
    class_stats jsonb,
    dominant_class integer,
    dominant_name text,
    classified_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.terrain_classifications OWNER TO aether;

--
-- Name: topographic_features; Type: TABLE; Schema: public; Owner: aether
--

CREATE TABLE public.topographic_features (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    region_id uuid NOT NULL,
    output_dir text NOT NULL,
    features_list text[] DEFAULT ARRAY['slope'::text, 'aspect'::text, 'curvature'::text, 'flow_accumulation'::text, 'twi'::text, 'roughness'::text],
    job_log text,
    generated_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.topographic_features OWNER TO aether;

--
-- Name: users; Type: TABLE; Schema: public; Owner: aether
--

CREATE TABLE public.users (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    email text NOT NULL,
    password text NOT NULL,
    role text NOT NULL,
    full_name text,
    created_at timestamp with time zone DEFAULT now(),
    CONSTRAINT users_role_check CHECK ((role = ANY (ARRAY['admin'::text, 'user'::text])))
);


ALTER TABLE public.users OWNER TO aether;

--
-- Name: villages_boundaries; Type: TABLE; Schema: public; Owner: aether
--

CREATE TABLE public.villages_boundaries (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    name text NOT NULL,
    taluka_name text,
    district_name text,
    state_name text,
    geom public.geometry(MultiPolygon,4326),
    centroid public.geometry(Point,4326),
    bbox public.geometry(Polygon,4326),
    source_fid integer,
    created_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.villages_boundaries OWNER TO aether;

--
-- Name: weather_data; Type: TABLE; Schema: public; Owner: aether
--

CREATE TABLE public.weather_data (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    country text NOT NULL,
    state text NOT NULL,
    date date NOT NULL,
    lon double precision NOT NULL,
    lat double precision NOT NULL,
    grid_id text NOT NULL,
    rain_mm double precision,
    max_intensity_mm_hr double precision,
    temperature_2m double precision,
    dewpoint_temperature_2m double precision,
    surface_pressure double precision,
    mean_sea_level_pressure double precision,
    wind_speed_ms double precision,
    wind_dir_deg double precision,
    soil_moisture_m3m3 double precision,
    surface_runoff_mm double precision,
    created_at timestamp with time zone DEFAULT now(),
    convective_precipitation_mm numeric,
    snowmelt_mm numeric,
    total_column_water_vapour_kg_m2 numeric,
    u10_ms numeric,
    v10_ms numeric,
    u100_ms numeric,
    v100_ms numeric,
    temperature_2m_k numeric,
    dewpoint_temperature_2m_k numeric,
    boundary_layer_height_m numeric,
    sea_surface_temperature_k numeric,
    mean_total_precipitation_rate_kg_m2s numeric,
    surface_net_solar_radiation_j_m2 numeric,
    potential_evaporation_m numeric,
    snow_cover_fraction numeric,
    mean_evaporation_rate_kg_m2s numeric,
    cape_j_kg numeric,
    cin_j_kg numeric,
    k_index_k numeric,
    cloud_base_height_m numeric,
    soil_moisture_layer1_m3m3 numeric,
    soil_moisture_layer2_m3m3 numeric,
    soil_temperature_level1_k numeric,
    lai_high numeric,
    lai_low numeric,
    relative_vorticity_850_s1 numeric,
    divergence_200_s1 numeric,
    vertical_velocity_500_pa_s numeric,
    specific_humidity_850_kg_kg numeric,
    relative_humidity_700_pct numeric,
    significant_wave_height_m numeric,
    peak_wave_period_s numeric,
    surface_runoff_m numeric,
    mean_sea_level_pressure_pa numeric
);


ALTER TABLE public.weather_data OWNER TO aether;

--
-- Name: migrations_history id; Type: DEFAULT; Schema: public; Owner: aether
--

ALTER TABLE ONLY public.migrations_history ALTER COLUMN id SET DEFAULT nextval('public.migrations_history_id_seq'::regclass);


--
-- Name: susceptibility_flood id; Type: DEFAULT; Schema: public; Owner: aether
--

ALTER TABLE ONLY public.susceptibility_flood ALTER COLUMN id SET DEFAULT nextval('public.susceptibility_flood_id_seq'::regclass);


--
-- Name: susceptibility_landslide id; Type: DEFAULT; Schema: public; Owner: aether
--

ALTER TABLE ONLY public.susceptibility_landslide ALTER COLUMN id SET DEFAULT nextval('public.susceptibility_landslide_id_seq'::regclass);


--
-- Name: data_inventory data_inventory_pkey; Type: CONSTRAINT; Schema: public; Owner: aether
--

ALTER TABLE ONLY public.data_inventory
    ADD CONSTRAINT data_inventory_pkey PRIMARY KEY (id);


--
-- Name: data_inventory data_inventory_region_id_key; Type: CONSTRAINT; Schema: public; Owner: aether
--

ALTER TABLE ONLY public.data_inventory
    ADD CONSTRAINT data_inventory_region_id_key UNIQUE (region_id);


--
-- Name: dem_uploads dem_uploads_pkey; Type: CONSTRAINT; Schema: public; Owner: aether
--

ALTER TABLE ONLY public.dem_uploads
    ADD CONSTRAINT dem_uploads_pkey PRIMARY KEY (id);


--
-- Name: dem_uploads dem_uploads_region_id_key; Type: CONSTRAINT; Schema: public; Owner: aether
--

ALTER TABLE ONLY public.dem_uploads
    ADD CONSTRAINT dem_uploads_region_id_key UNIQUE (region_id);


--
-- Name: disaster_types disaster_types_code_key; Type: CONSTRAINT; Schema: public; Owner: aether
--

ALTER TABLE ONLY public.disaster_types
    ADD CONSTRAINT disaster_types_code_key UNIQUE (code);


--
-- Name: disaster_types disaster_types_name_key; Type: CONSTRAINT; Schema: public; Owner: aether
--

ALTER TABLE ONLY public.disaster_types
    ADD CONSTRAINT disaster_types_name_key UNIQUE (name);


--
-- Name: disaster_types disaster_types_pkey; Type: CONSTRAINT; Schema: public; Owner: aether
--

ALTER TABLE ONLY public.disaster_types
    ADD CONSTRAINT disaster_types_pkey PRIMARY KEY (id);


--
-- Name: districts_boundaries districts_boundaries_name_state_name_key; Type: CONSTRAINT; Schema: public; Owner: aether
--

ALTER TABLE ONLY public.districts_boundaries
    ADD CONSTRAINT districts_boundaries_name_state_name_key UNIQUE (name, state_name);


--
-- Name: districts_boundaries districts_boundaries_pkey; Type: CONSTRAINT; Schema: public; Owner: aether
--

ALTER TABLE ONLY public.districts_boundaries
    ADD CONSTRAINT districts_boundaries_pkey PRIMARY KEY (id);


--
-- Name: india_coastlines india_coastlines_pkey; Type: CONSTRAINT; Schema: public; Owner: aether
--

ALTER TABLE ONLY public.india_coastlines
    ADD CONSTRAINT india_coastlines_pkey PRIMARY KEY (id);


--
-- Name: india_faults india_faults_pkey; Type: CONSTRAINT; Schema: public; Owner: aether
--

ALTER TABLE ONLY public.india_faults
    ADD CONSTRAINT india_faults_pkey PRIMARY KEY (id);


--
-- Name: india_rivers india_rivers_pkey; Type: CONSTRAINT; Schema: public; Owner: aether
--

ALTER TABLE ONLY public.india_rivers
    ADD CONSTRAINT india_rivers_pkey PRIMARY KEY (id);


--
-- Name: jobs jobs_pkey; Type: CONSTRAINT; Schema: public; Owner: aether
--

ALTER TABLE ONLY public.jobs
    ADD CONSTRAINT jobs_pkey PRIMARY KEY (id);


--
-- Name: manual_data_india manual_data_india_data_type_key; Type: CONSTRAINT; Schema: public; Owner: aether
--

ALTER TABLE ONLY public.manual_data_india
    ADD CONSTRAINT manual_data_india_data_type_key UNIQUE (data_type);


--
-- Name: manual_data_india manual_data_india_pkey; Type: CONSTRAINT; Schema: public; Owner: aether
--

ALTER TABLE ONLY public.manual_data_india
    ADD CONSTRAINT manual_data_india_pkey PRIMARY KEY (id);


--
-- Name: migrations_history migrations_history_file_name_key; Type: CONSTRAINT; Schema: public; Owner: aether
--

ALTER TABLE ONLY public.migrations_history
    ADD CONSTRAINT migrations_history_file_name_key UNIQUE (file_name);


--
-- Name: migrations_history migrations_history_pkey; Type: CONSTRAINT; Schema: public; Owner: aether
--

ALTER TABLE ONLY public.migrations_history
    ADD CONSTRAINT migrations_history_pkey PRIMARY KEY (id);


--
-- Name: rainfall_timeseries rainfall_timeseries_pkey; Type: CONSTRAINT; Schema: public; Owner: aether
--

ALTER TABLE ONLY public.rainfall_timeseries
    ADD CONSTRAINT rainfall_timeseries_pkey PRIMARY KEY (id);


--
-- Name: rainfall_timeseries rainfall_timeseries_region_id_date_hour_key; Type: CONSTRAINT; Schema: public; Owner: aether
--

ALTER TABLE ONLY public.rainfall_timeseries
    ADD CONSTRAINT rainfall_timeseries_region_id_date_hour_key UNIQUE (region_id, date, hour);


--
-- Name: regions regions_country_state_district_key; Type: CONSTRAINT; Schema: public; Owner: aether
--

ALTER TABLE ONLY public.regions
    ADD CONSTRAINT regions_country_state_district_key UNIQUE (country, state, district);


--
-- Name: regions regions_pkey; Type: CONSTRAINT; Schema: public; Owner: aether
--

ALTER TABLE ONLY public.regions
    ADD CONSTRAINT regions_pkey PRIMARY KEY (id);


--
-- Name: states_boundaries states_boundaries_name_key; Type: CONSTRAINT; Schema: public; Owner: aether
--

ALTER TABLE ONLY public.states_boundaries
    ADD CONSTRAINT states_boundaries_name_key UNIQUE (name);


--
-- Name: states_boundaries states_boundaries_pkey; Type: CONSTRAINT; Schema: public; Owner: aether
--

ALTER TABLE ONLY public.states_boundaries
    ADD CONSTRAINT states_boundaries_pkey PRIMARY KEY (id);


--
-- Name: susceptibility_flood susceptibility_flood_pkey; Type: CONSTRAINT; Schema: public; Owner: aether
--

ALTER TABLE ONLY public.susceptibility_flood
    ADD CONSTRAINT susceptibility_flood_pkey PRIMARY KEY (id);


--
-- Name: susceptibility_landslide susceptibility_landslide_pkey; Type: CONSTRAINT; Schema: public; Owner: aether
--

ALTER TABLE ONLY public.susceptibility_landslide
    ADD CONSTRAINT susceptibility_landslide_pkey PRIMARY KEY (id);


--
-- Name: susceptibility_results susceptibility_results_pkey; Type: CONSTRAINT; Schema: public; Owner: aether
--

ALTER TABLE ONLY public.susceptibility_results
    ADD CONSTRAINT susceptibility_results_pkey PRIMARY KEY (id);


--
-- Name: susceptibility_results susceptibility_results_region_disaster_unique; Type: CONSTRAINT; Schema: public; Owner: aether
--

ALTER TABLE ONLY public.susceptibility_results
    ADD CONSTRAINT susceptibility_results_region_disaster_unique UNIQUE (region_id, disaster_code);


--
-- Name: susceptibility_results susceptibility_results_region_id_disaster_code_key; Type: CONSTRAINT; Schema: public; Owner: aether
--

ALTER TABLE ONLY public.susceptibility_results
    ADD CONSTRAINT susceptibility_results_region_id_disaster_code_key UNIQUE (region_id, disaster_code);


--
-- Name: talukas_boundaries talukas_boundaries_name_district_name_state_name_key; Type: CONSTRAINT; Schema: public; Owner: aether
--

ALTER TABLE ONLY public.talukas_boundaries
    ADD CONSTRAINT talukas_boundaries_name_district_name_state_name_key UNIQUE (name, district_name, state_name);


--
-- Name: talukas_boundaries talukas_boundaries_pkey; Type: CONSTRAINT; Schema: public; Owner: aether
--

ALTER TABLE ONLY public.talukas_boundaries
    ADD CONSTRAINT talukas_boundaries_pkey PRIMARY KEY (id);


--
-- Name: terrain_classifications terrain_classifications_pkey; Type: CONSTRAINT; Schema: public; Owner: aether
--

ALTER TABLE ONLY public.terrain_classifications
    ADD CONSTRAINT terrain_classifications_pkey PRIMARY KEY (id);


--
-- Name: terrain_classifications terrain_classifications_region_id_key; Type: CONSTRAINT; Schema: public; Owner: aether
--

ALTER TABLE ONLY public.terrain_classifications
    ADD CONSTRAINT terrain_classifications_region_id_key UNIQUE (region_id);


--
-- Name: topographic_features topographic_features_pkey; Type: CONSTRAINT; Schema: public; Owner: aether
--

ALTER TABLE ONLY public.topographic_features
    ADD CONSTRAINT topographic_features_pkey PRIMARY KEY (id);


--
-- Name: topographic_features topographic_features_region_id_key; Type: CONSTRAINT; Schema: public; Owner: aether
--

ALTER TABLE ONLY public.topographic_features
    ADD CONSTRAINT topographic_features_region_id_key UNIQUE (region_id);


--
-- Name: users users_email_key; Type: CONSTRAINT; Schema: public; Owner: aether
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_email_key UNIQUE (email);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: aether
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: villages_boundaries villages_boundaries_pkey; Type: CONSTRAINT; Schema: public; Owner: aether
--

ALTER TABLE ONLY public.villages_boundaries
    ADD CONSTRAINT villages_boundaries_pkey PRIMARY KEY (id);


--
-- Name: weather_data weather_data_pkey; Type: CONSTRAINT; Schema: public; Owner: aether
--

ALTER TABLE ONLY public.weather_data
    ADD CONSTRAINT weather_data_pkey PRIMARY KEY (id);


--
-- Name: weather_data weather_data_state_grid_id_date_key; Type: CONSTRAINT; Schema: public; Owner: aether
--

ALTER TABLE ONLY public.weather_data
    ADD CONSTRAINT weather_data_state_grid_id_date_key UNIQUE (state, grid_id, date);


--
-- Name: idx_disaster_active; Type: INDEX; Schema: public; Owner: aether
--

CREATE INDEX idx_disaster_active ON public.disaster_types USING btree (is_active);


--
-- Name: idx_disaster_category; Type: INDEX; Schema: public; Owner: aether
--

CREATE INDEX idx_disaster_category ON public.disaster_types USING btree (category);


--
-- Name: idx_disaster_types_code; Type: INDEX; Schema: public; Owner: aether
--

CREATE INDEX idx_disaster_types_code ON public.disaster_types USING btree (code);


--
-- Name: idx_districts_geom; Type: INDEX; Schema: public; Owner: aether
--

CREATE INDEX idx_districts_geom ON public.districts_boundaries USING gist (geom);


--
-- Name: idx_districts_name; Type: INDEX; Schema: public; Owner: aether
--

CREATE INDEX idx_districts_name ON public.districts_boundaries USING btree (name);


--
-- Name: idx_districts_state; Type: INDEX; Schema: public; Owner: aether
--

CREATE INDEX idx_districts_state ON public.districts_boundaries USING btree (state_name);


--
-- Name: idx_gist_india_coastlines_geom; Type: INDEX; Schema: public; Owner: aether
--

CREATE INDEX idx_gist_india_coastlines_geom ON public.india_coastlines USING gist (geom);


--
-- Name: idx_gist_india_faults_geom; Type: INDEX; Schema: public; Owner: aether
--

CREATE INDEX idx_gist_india_faults_geom ON public.india_faults USING gist (geom);


--
-- Name: idx_gist_india_rivers_geom; Type: INDEX; Schema: public; Owner: aether
--

CREATE INDEX idx_gist_india_rivers_geom ON public.india_rivers USING gist (geom);


--
-- Name: idx_india_coastlines_geom; Type: INDEX; Schema: public; Owner: aether
--

CREATE INDEX idx_india_coastlines_geom ON public.india_coastlines USING gist (geom);


--
-- Name: idx_india_faults_geom; Type: INDEX; Schema: public; Owner: aether
--

CREATE INDEX idx_india_faults_geom ON public.india_faults USING gist (geom);


--
-- Name: idx_india_rivers_geom; Type: INDEX; Schema: public; Owner: aether
--

CREATE INDEX idx_india_rivers_geom ON public.india_rivers USING gist (geom);


--
-- Name: idx_jobs_status; Type: INDEX; Schema: public; Owner: aether
--

CREATE INDEX idx_jobs_status ON public.jobs USING btree (status);


--
-- Name: idx_rainfall_region_date; Type: INDEX; Schema: public; Owner: aether
--

CREATE INDEX idx_rainfall_region_date ON public.rainfall_timeseries USING btree (region_id, date);


--
-- Name: idx_regions_country; Type: INDEX; Schema: public; Owner: aether
--

CREATE INDEX idx_regions_country ON public.regions USING btree (country);


--
-- Name: idx_regions_geom; Type: INDEX; Schema: public; Owner: aether
--

CREATE INDEX idx_regions_geom ON public.regions USING gist (geom);


--
-- Name: idx_regions_state; Type: INDEX; Schema: public; Owner: aether
--

CREATE INDEX idx_regions_state ON public.regions USING btree (state);


--
-- Name: idx_states_geom; Type: INDEX; Schema: public; Owner: aether
--

CREATE INDEX idx_states_geom ON public.states_boundaries USING gist (geom);


--
-- Name: idx_states_name; Type: INDEX; Schema: public; Owner: aether
--

CREATE INDEX idx_states_name ON public.states_boundaries USING btree (name);


--
-- Name: idx_susc_disaster; Type: INDEX; Schema: public; Owner: aether
--

CREATE INDEX idx_susc_disaster ON public.susceptibility_results USING btree (disaster_code);


--
-- Name: idx_susc_region; Type: INDEX; Schema: public; Owner: aether
--

CREATE INDEX idx_susc_region ON public.susceptibility_results USING btree (region_id);


--
-- Name: idx_susc_region_disaster; Type: INDEX; Schema: public; Owner: aether
--

CREATE INDEX idx_susc_region_disaster ON public.susceptibility_results USING btree (region_id, disaster_code);


--
-- Name: idx_susceptibility_flood_region; Type: INDEX; Schema: public; Owner: aether
--

CREATE INDEX idx_susceptibility_flood_region ON public.susceptibility_flood USING btree (region_id);


--
-- Name: idx_susceptibility_landslide_region; Type: INDEX; Schema: public; Owner: aether
--

CREATE INDEX idx_susceptibility_landslide_region ON public.susceptibility_landslide USING btree (region_id);


--
-- Name: idx_talukas_geom; Type: INDEX; Schema: public; Owner: aether
--

CREATE INDEX idx_talukas_geom ON public.talukas_boundaries USING gist (geom);


--
-- Name: idx_talukas_name; Type: INDEX; Schema: public; Owner: aether
--

CREATE INDEX idx_talukas_name ON public.talukas_boundaries USING btree (name);


--
-- Name: idx_talukas_parent; Type: INDEX; Schema: public; Owner: aether
--

CREATE INDEX idx_talukas_parent ON public.talukas_boundaries USING btree (state_name, district_name);


--
-- Name: idx_villages_geom; Type: INDEX; Schema: public; Owner: aether
--

CREATE INDEX idx_villages_geom ON public.villages_boundaries USING gist (geom);


--
-- Name: idx_villages_name; Type: INDEX; Schema: public; Owner: aether
--

CREATE INDEX idx_villages_name ON public.villages_boundaries USING btree (name);


--
-- Name: idx_villages_parent; Type: INDEX; Schema: public; Owner: aether
--

CREATE INDEX idx_villages_parent ON public.villages_boundaries USING btree (state_name, district_name, taluka_name);


--
-- Name: idx_weather_data_grid; Type: INDEX; Schema: public; Owner: aether
--

CREATE INDEX idx_weather_data_grid ON public.weather_data USING btree (grid_id);


--
-- Name: idx_weather_data_state_date; Type: INDEX; Schema: public; Owner: aether
--

CREATE INDEX idx_weather_data_state_date ON public.weather_data USING btree (state, date);


--
-- Name: data_inventory data_inventory_region_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: aether
--

ALTER TABLE ONLY public.data_inventory
    ADD CONSTRAINT data_inventory_region_id_fkey FOREIGN KEY (region_id) REFERENCES public.regions(id) ON DELETE CASCADE;


--
-- Name: dem_uploads dem_uploads_region_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: aether
--

ALTER TABLE ONLY public.dem_uploads
    ADD CONSTRAINT dem_uploads_region_id_fkey FOREIGN KEY (region_id) REFERENCES public.regions(id) ON DELETE CASCADE;


--
-- Name: jobs jobs_region_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: aether
--

ALTER TABLE ONLY public.jobs
    ADD CONSTRAINT jobs_region_id_fkey FOREIGN KEY (region_id) REFERENCES public.regions(id) ON DELETE SET NULL;


--
-- Name: manual_data_india manual_data_india_uploaded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: aether
--

ALTER TABLE ONLY public.manual_data_india
    ADD CONSTRAINT manual_data_india_uploaded_by_fkey FOREIGN KEY (uploaded_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: rainfall_timeseries rainfall_timeseries_region_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: aether
--

ALTER TABLE ONLY public.rainfall_timeseries
    ADD CONSTRAINT rainfall_timeseries_region_id_fkey FOREIGN KEY (region_id) REFERENCES public.regions(id) ON DELETE CASCADE;


--
-- Name: susceptibility_results susceptibility_results_disaster_type_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: aether
--

ALTER TABLE ONLY public.susceptibility_results
    ADD CONSTRAINT susceptibility_results_disaster_type_id_fkey FOREIGN KEY (disaster_type_id) REFERENCES public.disaster_types(id) ON DELETE SET NULL;


--
-- Name: susceptibility_results susceptibility_results_region_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: aether
--

ALTER TABLE ONLY public.susceptibility_results
    ADD CONSTRAINT susceptibility_results_region_id_fkey FOREIGN KEY (region_id) REFERENCES public.regions(id) ON DELETE CASCADE;


--
-- Name: terrain_classifications terrain_classifications_region_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: aether
--

ALTER TABLE ONLY public.terrain_classifications
    ADD CONSTRAINT terrain_classifications_region_id_fkey FOREIGN KEY (region_id) REFERENCES public.regions(id) ON DELETE CASCADE;


--
-- Name: topographic_features topographic_features_region_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: aether
--

ALTER TABLE ONLY public.topographic_features
    ADD CONSTRAINT topographic_features_region_id_fkey FOREIGN KEY (region_id) REFERENCES public.regions(id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--


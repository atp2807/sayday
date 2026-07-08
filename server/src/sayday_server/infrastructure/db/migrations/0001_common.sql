CREATE OR REPLACE FUNCTION set_updated_ts() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_ts = now();
    RETURN NEW;
END;
$$;

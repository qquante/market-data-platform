\getenv app_password DATA_DB_PASSWORD
SELECT format('CREATE ROLE midas_app LOGIN PASSWORD %L', :'app_password') \gexec
CREATE DATABASE midas_data OWNER midas_app;
CREATE DATABASE midas_lake OWNER midas_app;

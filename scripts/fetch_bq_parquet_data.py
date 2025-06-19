import pyarrow as pa
import pyarrow.parquet as pq
from google.cloud import bigquery
from google.oauth2 import service_account
import os

def dump_bq_to_parquet_no_pandas(table_name:str):
    """
    Dump BigQuery table to local parquet file without pandas dependency
    """
    
    # Initialize BigQuery client
    # Option 1: Use default credentials (if gcloud auth is set up)
    client = bigquery.Client()
    
    # Option 2: Use service account key file (uncomment if needed)
    # credentials = service_account.Credentials.from_service_account_file("path/to/your/service-account-key.json")
    # client = bigquery.Client(credentials=credentials, project=credentials.project_id)
    
    # Define the query
    query = f"""
    SELECT *
    FROM `{table_name}`
    """
    
    # Configure query job to return results as Arrow format
    job_config = bigquery.QueryJobConfig()
    
    print("Querying BigQuery table...")
    query_job = client.query(query, job_config=job_config)
    
    # Wait for the query to complete and get results
    result = query_job.result()
    print('Query completed successfully.')

    # Convert to Arrow Table
    arrow_table = result.to_arrow()
    print(f"Retrieved {arrow_table.num_rows} rows from BigQuery")
    print(f"Schema: {arrow_table.schema}")
    
    # Define output file path
    file_name = table_name.split('.')[-1]  # Get the last part of the table name
    output_file = f'{file_name}.parquet'
    
    # Write directly to parquet using PyArrow
    print(f"Writing to {output_file}...")
    pq.write_table(arrow_table, output_file)
    
    print(f"Successfully exported data to {output_file}")
    
    # Verify the file
    file_size = os.path.getsize(output_file)
    print(f"File size: {file_size:,} bytes")
    
    # Optional: Read back and show sample
    print("\nSample data:")
    parquet_table = pq.read_table(output_file)
    sample = parquet_table.slice(0, 5).to_pandas()  # Just for display
    print(sample)
    
    print(f"\nTotal rows in parquet: {parquet_table.num_rows:,}")

if __name__ == "__main__":
    try:
        dump_bq_to_parquet_no_pandas('preqldata.public_geo.osm_cities')
    except Exception as e:
        print(f"Error: {e}")
        print("\nMake sure you have:")
        print("1. Installed required packages: pip install google-cloud-bigquery pyarrow")
        print("2. Set up Google Cloud authentication (gcloud auth login or service account)")
        print("3. Have access to the BigQuery table")
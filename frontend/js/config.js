// Weather App — API configuration
// Only the API Gateway endpoint lives here. No secrets, no API keys.
// The OpenWeatherMap key is stored in AWS SSM Parameter Store and
// read exclusively by the Lambda function — it never reaches this file.

const CONFIG = {
  // Route 53 Failover routing target — automatically shifts between
  // us-east-1 and us-west-2 based on the primary region's health check
  // (docs/WAPMultiRegion/WeatherApp-MultiRegion-ImplePlan-V1.md §3).
  API_BASE_URL: 'https://api.weather.craftingnewtech.com',
};

export default CONFIG;

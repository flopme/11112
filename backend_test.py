import requests
import sys
import json
import time
from datetime import datetime

class EthereumMempoolTester:
    def __init__(self, base_url="https://ethtracker.preview.emergentagent.com"):
        self.base_url = base_url
        self.api_url = f"{base_url}/api"
        self.tests_run = 0
        self.tests_passed = 0
        self.session = requests.Session()
        self.session.headers.update({'Content-Type': 'application/json'})

    def run_test(self, name, method, endpoint, expected_status, data=None, timeout=10):
        """Run a single API test"""
        url = f"{self.api_url}/{endpoint}" if endpoint else self.api_url
        
        self.tests_run += 1
        print(f"\n🔍 Testing {name}...")
        print(f"   URL: {url}")
        
        try:
            if method == 'GET':
                response = self.session.get(url, timeout=timeout)
            elif method == 'POST':
                response = self.session.post(url, json=data, timeout=timeout)
            else:
                raise ValueError(f"Unsupported method: {method}")

            success = response.status_code == expected_status
            if success:
                self.tests_passed += 1
                print(f"✅ Passed - Status: {response.status_code}")
                try:
                    response_data = response.json()
                    print(f"   Response: {json.dumps(response_data, indent=2)}")
                    return True, response_data
                except:
                    print(f"   Response (text): {response.text[:200]}...")
                    return True, response.text
            else:
                print(f"❌ Failed - Expected {expected_status}, got {response.status_code}")
                print(f"   Response: {response.text[:200]}...")
                return False, {}

        except requests.exceptions.Timeout:
            print(f"❌ Failed - Request timed out after {timeout}s")
            return False, {}
        except requests.exceptions.ConnectionError:
            print(f"❌ Failed - Connection error")
            return False, {}
        except Exception as e:
            print(f"❌ Failed - Error: {str(e)}")
            return False, {}

    def test_root_endpoint(self):
        """Test the root API endpoint"""
        return self.run_test("Root API Endpoint", "GET", "", 200)

    def test_stats_endpoint(self):
        """Test the stats endpoint"""
        return self.run_test("Stats Endpoint", "GET", "stats", 200)

    def test_start_monitoring(self):
        """Test starting monitoring"""
        return self.run_test("Start Monitoring", "POST", "start-monitoring", 200)

    def test_stop_monitoring(self):
        """Test stopping monitoring"""
        return self.run_test("Stop Monitoring", "POST", "stop-monitoring", 200)

    def test_get_transactions(self):
        """Test getting recent transactions"""
        return self.run_test("Get Transactions", "GET", "transactions", 200)

    def test_telegram_integration(self):
        """Test Telegram integration"""
        return self.run_test("Test Telegram", "POST", "test-telegram", 200, timeout=15)

    def test_transactions_with_limit(self):
        """Test getting transactions with limit parameter"""
        return self.run_test("Get Transactions (limit=10)", "GET", "transactions?limit=10", 200)

    def run_comprehensive_test(self):
        """Run all tests in sequence"""
        print("🚀 Starting Ethereum Mempool Monitor API Tests")
        print(f"🌐 Testing against: {self.base_url}")
        print("=" * 60)

        # Test 1: Basic API connectivity
        success, data = self.test_root_endpoint()
        if not success:
            print("\n❌ CRITICAL: Root API endpoint failed - stopping tests")
            return False

        # Test 2: Stats endpoint
        success, stats_data = self.test_stats_endpoint()
        if success:
            print(f"   📊 Current stats: {stats_data}")

        # Test 3: Get transactions (should work even if empty)
        success, transactions = self.test_get_transactions()
        if success:
            if isinstance(transactions, list):
                print(f"   📝 Found {len(transactions)} transactions")
            else:
                print(f"   📝 Transactions response: {type(transactions)}")

        # Test 4: Get transactions with limit
        self.test_transactions_with_limit()

        # Test 5: Test Telegram integration (this might take longer)
        print("\n⚠️  Testing Telegram integration (may take 10-15 seconds)...")
        success, telegram_result = self.test_telegram_integration()
        if success:
            print("   📱 Telegram integration working!")
        else:
            print("   ❌ Telegram integration failed")

        # Test 6: Start monitoring
        print("\n🔄 Testing monitoring control...")
        success, start_result = self.test_start_monitoring()
        if success:
            print("   ✅ Monitoring start command accepted")
            
            # Wait a moment and check stats
            print("   ⏳ Waiting 3 seconds to check if monitoring started...")
            time.sleep(3)
            
            success, updated_stats = self.test_stats_endpoint()
            if success:
                print(f"   📊 Updated stats: {updated_stats}")

        # Test 7: Stop monitoring
        success, stop_result = self.test_stop_monitoring()
        if success:
            print("   🛑 Monitoring stop command accepted")

        # Final stats check
        print("\n📊 Final stats check...")
        self.test_stats_endpoint()

        return True

    def print_summary(self):
        """Print test summary"""
        print("\n" + "=" * 60)
        print("📋 TEST SUMMARY")
        print("=" * 60)
        print(f"✅ Tests passed: {self.tests_passed}/{self.tests_run}")
        print(f"❌ Tests failed: {self.tests_run - self.tests_passed}/{self.tests_run}")
        
        if self.tests_passed == self.tests_run:
            print("\n🎉 ALL TESTS PASSED! The API is working correctly.")
            return 0
        else:
            print(f"\n⚠️  {self.tests_run - self.tests_passed} test(s) failed. Check the issues above.")
            return 1

def main():
    """Main test function"""
    print("🧪 Ethereum Mempool Monitor - Backend API Testing")
    print(f"⏰ Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    tester = EthereumMempoolTester()
    
    try:
        tester.run_comprehensive_test()
    except KeyboardInterrupt:
        print("\n⚠️  Tests interrupted by user")
    except Exception as e:
        print(f"\n❌ Unexpected error during testing: {e}")
    
    return tester.print_summary()

if __name__ == "__main__":
    sys.exit(main())
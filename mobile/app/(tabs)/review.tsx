import { useLocalSearchParams } from 'expo-router';
import { useEffect, useState } from 'react';
import { StyleSheet, Text, View, ActivityIndicator, ScrollView, TouchableOpacity } from 'react-native';
import axios from 'axios';

const API_URL = 'http://192.168.0.107:8080/v1/documents';

export default function ReviewScreen() {
  const { docId } = useLocalSearchParams();
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (docId) {
      pollDocument(docId as string);
    }
  }, [docId]);

  const pollDocument = async (id: string) => {
    try {
      const response = await axios.get(`${API_URL}/${id}`);
      if (response.data.status === 'processing') {
        setTimeout(() => pollDocument(id), 2000);
      } else {
        setData(response.data);
        setLoading(false);
      }
    } catch (error) {
      console.error(error);
      setLoading(false);
    }
  };

  const approveDocument = async () => {
    if (!docId) return;
    try {
      await axios.post(`${API_URL}/${docId}/approve`);
      alert('Document Approved and Synced!');
    } catch (error) {
      console.error(error);
      alert('Approval failed');
    }
  };

  if (!docId) {
    return (
      <View style={styles.container}>
        <Text style={styles.text}>No document selected for review.</Text>
      </View>
    );
  }

  if (loading) {
    return (
      <View style={styles.container}>
        <ActivityIndicator size="large" color="#0000ff" />
        <Text style={styles.text}>Processing OCR...</Text>
      </View>
    );
  }

  if (!data) {
    return (
      <View style={styles.container}>
        <Text style={styles.text}>Error loading document data.</Text>
      </View>
    );
  }

  return (
    <ScrollView contentContainerStyle={styles.scrollContainer}>
      <Text style={styles.header}>Review Prescription</Text>
      
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Patient Info</Text>
        <Text>Name: {data.patient?.name?.value}</Text>
        <Text>Age: {data.patient?.age?.value}</Text>
      </View>

      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Medications</Text>
        {data.medications?.map((med: any, index: number) => (
          <View key={index} style={styles.medItem}>
            <Text style={styles.medName}>{med.name?.value}</Text>
            <Text>Strength: {med.strength?.value}</Text>
            <Text>Frequency: {med.frequency?.value} ({med.frequency?.expanded})</Text>
            <Text>Duration: {med.duration?.value}</Text>
          </View>
        ))}
      </View>

      <TouchableOpacity style={styles.approveBtn} onPress={approveDocument}>
        <Text style={styles.btnText}>Approve & Sync</Text>
      </TouchableOpacity>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
  },
  scrollContainer: {
    padding: 20,
    paddingBottom: 40,
  },
  header: {
    fontSize: 24,
    fontWeight: 'bold',
    marginBottom: 20,
  },
  section: {
    backgroundColor: '#f9f9f9',
    padding: 15,
    borderRadius: 8,
    marginBottom: 15,
  },
  sectionTitle: {
    fontSize: 18,
    fontWeight: '600',
    marginBottom: 10,
  },
  text: {
    fontSize: 16,
    marginTop: 10,
  },
  medItem: {
    marginBottom: 10,
    borderBottomWidth: 1,
    borderBottomColor: '#eee',
    paddingBottom: 10,
  },
  medName: {
    fontSize: 16,
    fontWeight: 'bold',
  },
  approveBtn: {
    backgroundColor: '#007AFF',
    padding: 15,
    borderRadius: 8,
    alignItems: 'center',
    marginTop: 20,
  },
  btnText: {
    color: 'white',
    fontSize: 18,
    fontWeight: 'bold',
  }
});

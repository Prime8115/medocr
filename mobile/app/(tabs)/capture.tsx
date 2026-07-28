import { CameraView, useCameraPermissions } from 'expo-camera';
import * as DocumentPicker from 'expo-document-picker';
import * as ImageManipulator from 'expo-image-manipulator';
import * as ImagePicker from 'expo-image-picker';
import NetInfo from '@react-native-community/netinfo';
import { router } from 'expo-router';
import { useRef, useState } from 'react';
import { Image, ScrollView, StyleSheet, Text, TouchableOpacity, View } from 'react-native';

import { uploadDocument } from '@/src/api/documents';
import { useQueue } from '@/src/queue/QueueContext';
import { Badge, Button, Screen } from '@/src/theme/components';
import { colors, font, radius, spacing } from '@/src/theme/tokens';
import { IMAGE_COMPRESSION, MAX_IMAGE_DIMENSION } from '@/src/config';
import { t } from '@/src/i18n/strings';

type DocType = 'prescription' | 'invoice' | undefined;

interface Picked {
  uri: string;
  type: 'image/jpeg' | 'application/pdf';
  name: string;
}

async function compressImage(uri: string): Promise<string> {
  try {
    const result = await ImageManipulator.manipulateAsync(
      uri,
      [{ resize: { width: MAX_IMAGE_DIMENSION } }],
      { compress: IMAGE_COMPRESSION, format: ImageManipulator.SaveFormat.JPEG },
    );
    return result.uri;
  } catch {
    return uri; // fall back to original on any failure
  }
}

export default function CaptureScreen() {
  const [permission, requestPermission] = useCameraPermissions();
  const cameraRef = useRef<CameraView>(null);
  const [ready, setReady] = useState(false);
  const [picked, setPicked] = useState<Picked | null>(null);
  const [docType, setDocType] = useState<DocType>(undefined);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const { add } = useQueue();

  if (!permission) return <Screen />;

  if (!permission.granted) {
    return (
      <Screen>
        <View style={styles.permission}>
          <Text style={styles.permissionText}>{t('cameraPermission')}</Text>
          <Button title={t('grantPermission')} onPress={requestPermission} />
        </View>
      </Screen>
    );
  }

  async function takePicture() {
    if (!cameraRef.current || !ready) return;
    const photo = await cameraRef.current.takePictureAsync();
    if (photo?.uri) {
      const uri = await compressImage(photo.uri);
      setPicked({ uri, type: 'image/jpeg', name: 'scan.jpg' });
    }
  }

  async function pickImage() {
    const result = await ImagePicker.launchImageLibraryAsync({ mediaTypes: ['images'], quality: 1 });
    if (!result.canceled && result.assets[0]) {
      const uri = await compressImage(result.assets[0].uri);
      setPicked({ uri, type: 'image/jpeg', name: 'upload.jpg' });
    }
  }

  async function pickPdf() {
    const result = await DocumentPicker.getDocumentAsync({ type: 'application/pdf', copyToCacheDirectory: true });
    if (!result.canceled && result.assets[0]) {
      setPicked({ uri: result.assets[0].uri, type: 'application/pdf', name: result.assets[0].name || 'document.pdf' });
    }
  }

  async function process() {
    if (!picked) return;
    setBusy(true);
    setNote(null);
    try {
      const net = await NetInfo.fetch();
      if (net.isConnected) {
        const id = await uploadDocument({ uri: picked.uri, name: picked.name, type: picked.type }, docType);
        setPicked(null);
        router.push(`/review/${id}`);
      } else {
        await add({ uri: picked.uri, fileName: picked.name, contentType: picked.type, docType });
        setPicked(null);
        setNote(t('offline'));
        router.push('/(tabs)/history');
      }
    } catch {
      // Network hiccup mid-upload: fall back to the offline queue.
      await add({ uri: picked.uri, fileName: picked.name, contentType: picked.type, docType });
      setPicked(null);
      setNote(t('offline'));
      router.push('/(tabs)/history');
    } finally {
      setBusy(false);
    }
  }

  if (picked) {
    return (
      <Screen>
        <ScrollView contentContainerStyle={styles.previewWrap}>
          {picked.type === 'application/pdf' ? (
            <View style={styles.pdfBox}>
              <Text style={styles.pdfText}>📄 PDF selected</Text>
            </View>
          ) : (
            <Image source={{ uri: picked.uri }} style={styles.preview} resizeMode="contain" />
          )}

          <Text style={styles.chooseLabel}>Document type</Text>
          <View style={styles.chips}>
            {(
              [
                [undefined, t('autoDetect')],
                ['prescription', t('prescription')],
                ['invoice', t('invoice')],
              ] as [DocType, string][]
            ).map(([value, label]) => (
              <TouchableOpacity
                key={label}
                onPress={() => setDocType(value)}
                style={[styles.chip, docType === value && styles.chipActive]}
              >
                <Text style={[styles.chipText, docType === value && styles.chipTextActive]}>{label}</Text>
              </TouchableOpacity>
            ))}
          </View>

          <View style={styles.actionRow}>
            <Button title={t('retake')} variant="secondary" onPress={() => setPicked(null)} style={styles.half} />
            <Button title={t('process')} onPress={process} loading={busy} style={styles.half} />
          </View>
        </ScrollView>
      </Screen>
    );
  }

  return (
    <Screen>
      <CameraView ref={cameraRef} style={styles.camera} facing="back" onCameraReady={() => setReady(true)}>
        <View style={styles.frame} pointerEvents="none" />
        <Text style={styles.frameHint}>{t('framingHint')}</Text>
      </CameraView>

      {note ? (
        <View style={styles.noteBar}>
          <Badge label={note} tone="warning" />
        </View>
      ) : null}

      <View style={styles.controls}>
        <TouchableOpacity style={styles.sideBtn} onPress={pickImage}>
          <Text style={styles.sideText}>{t('gallery')}</Text>
        </TouchableOpacity>
        <TouchableOpacity style={styles.shutter} onPress={takePicture} disabled={!ready}>
          <View style={styles.shutterInner} />
        </TouchableOpacity>
        <TouchableOpacity style={styles.sideBtn} onPress={pickPdf}>
          <Text style={styles.sideText}>{t('pdf')}</Text>
        </TouchableOpacity>
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  permission: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: spacing.xl, gap: spacing.lg },
  permissionText: { ...font.body, color: colors.text, textAlign: 'center' },
  camera: { flex: 1, justifyContent: 'center', alignItems: 'center' },
  frame: {
    width: '82%',
    height: '62%',
    borderWidth: 3,
    borderColor: 'rgba(255,255,255,0.85)',
    borderRadius: radius.lg,
  },
  frameHint: { ...font.body, color: colors.white, marginTop: spacing.lg, backgroundColor: 'rgba(0,0,0,0.4)', paddingHorizontal: spacing.md, paddingVertical: spacing.xs, borderRadius: radius.pill },
  noteBar: { padding: spacing.md, alignItems: 'center', backgroundColor: colors.surface },
  controls: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-around',
    paddingVertical: spacing.lg,
    backgroundColor: colors.surface,
  },
  sideBtn: { minWidth: 64, alignItems: 'center', paddingVertical: spacing.md },
  sideText: { ...font.h3, color: colors.primary },
  shutter: {
    width: 74,
    height: 74,
    borderRadius: 37,
    borderWidth: 4,
    borderColor: colors.primary,
    alignItems: 'center',
    justifyContent: 'center',
  },
  shutterInner: { width: 56, height: 56, borderRadius: 28, backgroundColor: colors.primary },
  previewWrap: { padding: spacing.lg },
  preview: { width: '100%', height: 360, borderRadius: radius.lg, backgroundColor: colors.surfaceAlt },
  pdfBox: { width: '100%', height: 220, borderRadius: radius.lg, backgroundColor: colors.surfaceAlt, alignItems: 'center', justifyContent: 'center' },
  pdfText: { ...font.h2, color: colors.textSecondary },
  chooseLabel: { ...font.label, color: colors.textSecondary, marginTop: spacing.xl, marginBottom: spacing.sm, textTransform: 'uppercase' },
  chips: { flexDirection: 'row', gap: spacing.sm, flexWrap: 'wrap' },
  chip: { paddingHorizontal: spacing.lg, paddingVertical: spacing.sm, borderRadius: radius.pill, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.surface },
  chipActive: { backgroundColor: colors.primaryTint, borderColor: colors.primary },
  chipText: { ...font.body, color: colors.textSecondary },
  chipTextActive: { color: colors.primaryDark, fontWeight: '600' },
  actionRow: { flexDirection: 'row', gap: spacing.md, marginTop: spacing.xl },
  half: { flex: 1 },
});
